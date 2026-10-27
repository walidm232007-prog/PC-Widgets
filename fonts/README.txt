Inter, the typeface the board is set in, lives here:

  InterVariable.ttf          every weight, Thin to Black, and the optical-size axis
  InterVariable-Italic.ttf   the same, italic
  LICENSE-Inter.txt          the SIL Open Font License 1.1 they ship under

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
