"""The release number and who publishes it: the exe's properties, the installer
and the app read them from here. Raise VERSION before tagging a release
(packaging/README.md)."""

VERSION = "1.0.2"

# The publisher in the exe's properties, the installer's, and Settings -> Apps.
# Until now all three said "PC Widgets", which is the product, not who made it.
# A code signature is a separate name, carried by the certificate, not this.
PUBLISHER = "Studio Hendrixx"
COPYRIGHT = "Copyright © 2026 Studio Hendrixx"
HOMEPAGE = "https://github.com/walidm232007-prog/PC-Widgets"
