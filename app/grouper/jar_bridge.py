"""Sandboxed subprocess bridge to the CMS PDPM Grouper JAR (V2.4000) — v1 spec §13.

Drives the grouper through the thin I/O shim (``grouper-jar/RecaptureGrouperShim.java``, which
calls the documented public API ``Pdpm#xmlStringExec``). The shim does NO classification — the JAR
owns that (invariant #1; do not reinvent the JAR's logic).

The subprocess touches PHI-bearing MDS XML, so it runs under a sandbox contract:
  * input via a private tempdir (``mkdtemp`` then chmod 0700; file written O_EXCL 0600);
  * tempfiles deleted on success AND failure (rmtree in finally);
  * JAR SHA-256 verified against the manifest pin + Java major == 17 BEFORE exec
    (SHA mismatch → hard GrouperIntegrityError, never a skip; missing JVM/jar → GrouperUnavailable so
     the gated test SKIPS);
  * stdout/stderr REDACTED on error (only rc + sha256(mds_xml) escape — never raw output, which can
    echo PHI item values);
  * raw XML NEVER logged — only ``sha256(mds_xml)``;
  * timeout + memory cap (``-Xmx`` + best-effort RLIMIT_AS).
"""

from __future__ import annotations

import hashlib
import os
import re
import resource
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

from ..config.manifest import get_manifest
from ..config.settings import REPO_ROOT, grouper_config

_SHIM_CLASS = "RecaptureGrouperShim"
_LOG_OFF = "-Dorg.slf4j.simpleLogger.defaultLogLevel=OFF"
_SHIM_SRC = REPO_ROOT / "grouper-jar" / "RecaptureGrouperShim.java"
_JAVA_VERSION_RE = re.compile(r'version "(\d+)')


class GrouperUnavailable(RuntimeError):
    """JVM or grouper jar not present/runnable — caller should skip or fall back."""


class GrouperIntegrityError(RuntimeError):
    """The discovered JAR does not match the manifest-pinned SHA-256. Hard failure — never skipped."""


class GrouperError(RuntimeError):
    """The grouper ran but returned an error / no HIPPS. Message is REDACTED (no raw stdout/stderr)."""


class GrouperTimeout(GrouperError):
    """The grouper exceeded its execution deadline."""


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class JarBridge:
    def __init__(
        self,
        jar_dir: Path | None = None,
        java: str = "java",
        timeout_seconds: int = 30,
        memory_cap_mb: int = 1024,
        shim_src: Path | None = None,
        build_dir: Path | None = None,
    ):
        self.jar_dir = Path(jar_dir) if jar_dir is not None else grouper_config()["jar_dir"]
        self.java = java
        self.timeout = timeout_seconds
        self.memory_cap_mb = memory_cap_mb
        self.shim_src = shim_src or _SHIM_SRC
        self.build_dir = build_dir or (self.jar_dir.parent / "_shim_build")
        self._ready = False

    @classmethod
    def from_config(cls) -> JarBridge:
        cfg = grouper_config()
        return cls(
            cfg["jar_dir"],
            cfg["java"],
            cfg["timeout_seconds"],
            cfg["memory_cap_mb"],
        )

    # --- discovery ---------------------------------------------------------
    def _grouper_jar(self) -> Path:
        if not self.jar_dir.exists():
            raise GrouperUnavailable(f"grouper jar_dir not found: {self.jar_dir}")
        jars = [
            p
            for p in self.jar_dir.glob("snf-component-*.jar")
            if "-sources" not in p.name and "-qa-test" not in p.name
        ]
        if not jars:
            raise GrouperUnavailable(f"grouper jar (snf-component-*.jar) not found in {self.jar_dir}")
        return sorted(jars)[0]

    def _lib_glob(self) -> str:
        return str(self.jar_dir / "lib" / "*")

    def _classpath(self) -> str:
        return os.pathsep.join([str(self.build_dir), str(self._grouper_jar()), self._lib_glob()])

    def _javac(self) -> str:
        jp = Path(self.java)
        if jp.name == "java" and str(jp.parent) not in (".", ""):
            cand = jp.with_name("javac")
            if cand.exists():
                return str(cand)
        return "javac"

    # --- integrity (v1 §13: verify BEFORE exec) ---------------------------
    def _verify_jar(self, jar: Path) -> None:
        digest = hashlib.sha256(jar.read_bytes()).hexdigest()
        pinned = get_manifest().grouper_jar_sha256
        if digest != pinned:
            raise GrouperIntegrityError(
                f"grouper JAR SHA-256 mismatch: discovered {digest[:16]}… != pinned {pinned[:16]}… "
                f"(jar={jar.name}). Refusing to exec a JAR that does not match the regulatory manifest."
            )

    def _verify_java(self) -> None:
        try:
            proc = subprocess.run(
                [self.java, "-version"], capture_output=True, text=True, timeout=self.timeout
            )
        except (FileNotFoundError, OSError) as exc:
            raise GrouperUnavailable(f"java not runnable ({self.java!r}): {exc}") from exc
        out = (proc.stderr or "") + (proc.stdout or "")
        m = _JAVA_VERSION_RE.search(out)
        major = int(m.group(1)) if m else 0
        required = get_manifest().grouper_java_runtime
        if major < required:
            raise GrouperUnavailable(f"Java {major} < required {required} for the grouper")

    # --- lifecycle ---------------------------------------------------------
    def available(self) -> bool:
        """True if the JVM + grouper jar are present, runnable, and SHA-matched. A SHA mismatch is
        NOT caught here — it raises GrouperIntegrityError so a tampered JAR fails loudly, never skips."""
        try:
            self._ensure_ready()
            return True
        except GrouperUnavailable:
            return False

    def _ensure_ready(self) -> None:
        if self._ready:
            return
        jar = self._grouper_jar()
        self._verify_jar(jar)  # integrity FIRST — GrouperIntegrityError propagates (no skip)
        self._verify_java()
        self._compile_if_needed(jar)
        self._ready = True

    def _compile_if_needed(self, jar: Path) -> None:
        cls = self.build_dir / f"{_SHIM_CLASS}.class"
        if cls.exists():
            if not self.shim_src.exists() or cls.stat().st_mtime >= self.shim_src.stat().st_mtime:
                return
        if not self.shim_src.exists():
            raise GrouperUnavailable("compiled grouper shim is unavailable")
        self.build_dir.mkdir(parents=True, exist_ok=True)
        cp = os.pathsep.join([str(jar), self._lib_glob()])
        try:
            proc = subprocess.run(
                [self._javac(), "-cp", cp, "-d", str(self.build_dir), str(self.shim_src)],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except (FileNotFoundError, OSError) as exc:
            raise GrouperUnavailable(f"javac not runnable: {exc}") from exc
        if proc.returncode != 0:
            # Redacted: the shim source carries no PHI, but keep the discipline of not echoing raw output.
            raise GrouperUnavailable(f"shim compile failed (rc={proc.returncode})")

    def _preexec_memcap(self) -> None:
        """Best-effort address-space cap (POSIX). Belt-and-suspenders to the JVM -Xmx; some platforms
        (macOS) ignore RLIMIT_AS, so failures here are non-fatal."""
        try:
            cap = self.memory_cap_mb * 1024 * 1024 * 4  # AS includes JVM overhead; 4× the heap cap
            resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
        except (ValueError, OSError):
            pass

    # --- run (sandboxed) ---------------------------------------------------
    def run(self, xml: str) -> tuple[str, str, list[str]]:
        """Group one MDS XML assessment in a sandbox. Returns (hipps, version, errors).

        On any failure the tempdir is removed and the exception carries ONLY rc + sha256(xml) —
        never raw stdout/stderr (which may echo PHI item values)."""
        self._ensure_ready()
        xml_sha = sha256_text(xml)
        workdir = Path(tempfile.mkdtemp(prefix="grouper_"))
        try:
            os.chmod(workdir, 0o700)
            xml_path = workdir / "assessment.xml"
            fd = os.open(xml_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(xml)
            try:
                proc = subprocess.Popen(
                    [
                        self.java,
                        f"-Xmx{self.memory_cap_mb}m",
                        "-cp",
                        self._classpath(),
                        _LOG_OFF,
                        _SHIM_CLASS,
                        str(xml_path),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    preexec_fn=self._preexec_memcap,
                    start_new_session=True,
                )
                stdout, _stderr = proc.communicate(timeout=self.timeout)
            except subprocess.TimeoutExpired as exc:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    proc.kill()
                proc.communicate()
                raise GrouperTimeout(
                    f"grouper timed out after {self.timeout}s (xml_sha256={xml_sha[:16]})"
                ) from exc
            if proc.returncode != 0:
                # REDACTED: never include proc.stdout / proc.stderr.
                raise GrouperError(
                    f"grouper invocation failed (rc={proc.returncode}, xml_sha256={xml_sha[:16]})"
                )
            return self._parse(stdout)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    @staticmethod
    def _parse(out: str) -> tuple[str, str, list[str]]:
        hipps = version = ""
        errors: list[str] = []
        for line in out.splitlines():
            key, _, value = line.partition("\t")
            if key == "HIPPS":
                hipps = value.strip()
            elif key == "VERSION":
                version = value.strip()
            elif key == "ERRORS":
                errors = [e for e in value.strip().split("|") if e]
        return hipps, version, errors
