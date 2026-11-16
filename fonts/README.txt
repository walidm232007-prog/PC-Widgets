Inter, the typeface the board is set in, lives here:

  InterVariable.ttf          every weight, Thin to Black, and the optical-size axis
  InterVariable-Italic.ttf   the same, italic
  LICENSE-Inter.txt          the SIL Open Font License 1.1 they ship under

The clock's small Digital face is set in Oswald:

  Oswald-Variable.ttf        weights 200 to 700, from Google Fonts, unmodified
  LICENSE-Oswald.txt         the SIL Open Font License 1.1 it ships under

The faces any widget can be set in from its right-click Font menu, each from
the Google Fonts repository, unmodified, with its own licence beside it as
LICENSE-<name>.txt (all SIL OFL 1.1). They are listed, with their groups, in
FONT_CHOICES in widgets/core/typography.py:

  Manrope-Variable.ttf
  DMSans-Variable.ttf
  Outfit-Variable.ttf
  PlusJakartaSans-Variable.ttf
  SpaceGrotesk-Variable.ttf
  Sora-Variable.ttf
  Nunito-Variable.ttf
  Quicksand-Variable.ttf
  PlayfairDisplay-Variable.ttf
  Lora-Variable.ttf
  DMSerifDisplay-Regular.ttf
  BebasNeue-Regular.ttf
  JetBrainsMono-Variable.ttf
  Caveat-Variable.ttf
  HankenGrotesk-Variable.ttf
  SchibstedGrotesk-Variable.ttf
  FamiljenGrotesk-Variable.ttf
  BricolageGrotesque-Variable.ttf
  DarkerGrotesque-Variable.ttf
  HostGrotesk-Variable.ttf
  InstrumentSans-Variable.ttf
  Archivo-Variable.ttf
  Geist-Variable.ttf
  AlbertSans-Variable.ttf
  PublicSans-Variable.ttf

They are from Inter's official 4.1 release (github.com/rsms/inter), unmodified.
The OFL lets them travel with the app, so they are committed - keep the licence
file with them.

They are loaded with addApplicationFont, for this app's own process only -
nothing is installed system-wide and nothing else on the machine changes.

Don't add other fonts here unless their licence allows redistribution:
.gitignore keeps every other font file in this folder out of the repository,
and tools/prepublish.py refuses to publish one. Apple's SF Pro in particular may
not be shipped, which is why the board is set in Inter.

If the files are missing, the type stack falls back to Helvetica Neue, then
Segoe UI Variable (widgets/core/typography.py).
