# Bundled fonts

All fonts are served from this folder; the app never contacts a font CDN.

## Inter (user interface)

`Inter-Regular.woff`, `Inter-Medium.woff`, `Inter-SemiBold.woff`,
`Inter-Bold.woff` are Latin + Latin Extended-A subsets (including Māori
macrons ā ē ī ō ū) of Inter 4 by The Inter Project Authors,
https://github.com/rsms/inter. Licensed under the SIL Open Font License 1.1;
see `Inter-OFL.txt`. Subset with fontTools (`pyftsubset`, WOFF flavour).

## Noto Sans (PDF exports)

Unmodified `NotoSans-Regular.ttf` from the Noto Project:
https://github.com/notofonts/noto-fonts/blob/main/hinted/ttf/NotoSans/NotoSans-Regular.ttf

Licensed under SIL OFL 1.1; see `OFL.txt`. Embedded in PDF reports so
macrons and accents print correctly.

SHA-256: `b85c38ecea8a7cfb39c24e395a4007474fa5a4fc864f6ee33309eb4948d232d5`.
