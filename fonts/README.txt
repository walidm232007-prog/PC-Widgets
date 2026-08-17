Drop SF Pro's .otf files in this folder and restart the board:

  SF-Pro-Text-Regular.otf
  SF-Pro-Text-Semibold.otf
  SF-Pro-Display-Light.otf
  SF-Pro-Display-Bold.otf

Free from developer.apple.com/fonts (needs an Apple ID). The files are not
included, and .gitignore keeps them out of the repository: Apple's licence covers
designing user interfaces for Apple platforms and does not permit redistributing
the fonts. Please don't commit them.

They are loaded with addApplicationFont, for this app's own process only -
nothing is installed system-wide and nothing else on the machine changes.

The board looks good without them. The type stack falls back to Inter, then
Helvetica Neue, then Segoe UI Variable, and the two things that do most of the
work - Apple's size-dependent tracking curve and the optical-size split at 20pt -
are implemented in widgets/core/typography.py either way.
