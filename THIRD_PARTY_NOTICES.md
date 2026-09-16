# Third-Party Notices

The authored Python, Java shim, JavaScript, documentation, and tests in this repository are licensed
under the MIT License. Third-party components retain their own licenses.

## CMS PDPM Grouper

The application downloads PDPM Grouper JAR Package V2.4000 from the official CMS MDS Technical
Information page during bootstrap/build. The archive and runtime JAR hashes are pinned in
`cms-grouper.lock.json`. CMS binaries and PDFs are not committed to this repository.

The upstream package includes `OpenSourceLicenseDisclosures.pdf` and
`PDPM Grouper JAR Package V2.4000.pdf`; the bootstrap places them under the downloaded package's
`notices/` directory and the Docker build retains them under `/opt/cms-grouper/notices`.

## Runtime tools

- Tesseract OCR: Apache License 2.0.
- Poppler: GNU General Public License, version 2 or later, with component-specific notices.
- Eclipse Temurin / OpenJDK 17: GNU General Public License, version 2, with the Classpath Exception.

Python and JavaScript dependency license metadata is available from their respective locked package
manifests. A public prebuilt image is intentionally not distributed by this project.
