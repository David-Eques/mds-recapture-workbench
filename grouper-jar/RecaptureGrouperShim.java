// Thin I/O adapter around the official CMS PDPM Grouper (V2.4000).
//
// It performs NO classification — it reads one MDS assessment as an XML string
// (the documented public input format) and prints the grouper's computed HIPPS,
// product version, and any errors. All grouping logic lives in the CMS JAR
// (gov.cms.grouper.snf.app.Pdpm#xmlStringExec — the integration method CMS's own
// docs describe). This keeps invariant #4 intact: we call the JAR, we do not
// reimplement or guess its logic.
//
// Contract:
//   stdin  -> a single <ASSESSMENT>...</ASSESSMENT> MDS XML document (UTF-8)
//   stdout -> three tab-separated lines, machine-parsed by jar_bridge.py:
//               HIPPS\t<5-char code or empty>
//               VERSION\t<product version, e.g. 2.4000>
//               ERRORS\t<pipe-joined error strings, empty if none>
//   exit   -> 0 on success; non-zero (stack trace on stderr) if the JAR throws.
//
// Compiled on first use by jar_bridge.py against the discovered grouper jar; the
// .class output lives under the gitignored vendor/ tree.
import gov.cms.grouper.snf.app.Pdpm;
import gov.cms.grouper.snf.transfer.SnfClaim;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.List;

public class RecaptureGrouperShim {

  public static void main(String[] args) throws Exception {
    String xml = (args.length > 0)
        ? new String(Files.readAllBytes(Paths.get(args[0])), StandardCharsets.UTF_8)
        : readAll(System.in);

    SnfClaim claim = new Pdpm().xmlStringExec(xml);

    String hipps = (claim == null || claim.getHippsCode() == null) ? "" : claim.getHippsCode();
    String version = (claim == null) ? "" : claim.getRecalculated_z0100b();
    String errors = "";
    if (claim == null) {
      errors = "null-claim";
    } else {
      List<String> errs = claim.getErrors();
      errors = (errs == null) ? "" : String.join("|", errs);
    }

    StringBuilder out = new StringBuilder();
    out.append("HIPPS\t").append(hipps).append('\n');
    out.append("VERSION\t").append(version == null ? "" : version).append('\n');
    out.append("ERRORS\t").append(errors).append('\n');
    System.out.print(out);
  }

  private static String readAll(InputStream in) throws Exception {
    ByteArrayOutputStream buf = new ByteArrayOutputStream();
    byte[] chunk = new byte[8192];
    int n;
    while ((n = in.read(chunk)) != -1) {
      buf.write(chunk, 0, n);
    }
    return new String(buf.toByteArray(), StandardCharsets.UTF_8);
  }
}
