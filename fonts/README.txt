Inter.ttf is bundled deliberately.

Without a font shipped alongside the code, the app falls back to whatever the
machine happens to have — Arial on a Mac, DejaVu Sans on a Linux server,
Arial on Windows — and the same lesson comes out looking different depending
on who rendered it. Shipping one file makes every deployment identical.

It is a variable font carrying every weight from Thin to Black, so one file
covers both the regular body text and the bold headers.

Inter is licensed under the SIL Open Font License 1.1, which permits bundling
and redistribution: https://github.com/rsms/inter

TO USE YOUR OWN BRAND FONT INSTEAD
Drop the .ttf or .otf files in this folder with "Regular" and "Bold" in their
names, and delete Inter.ttf. Or point at specific files without deleting
anything:

    OVERLAY_FONT=/path/to/Regular.ttf
    OVERLAY_FONT_BOLD=/path/to/Bold.ttf
