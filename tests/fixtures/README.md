# Public regression images

All frames use a 1280×720 canvas. Black regions are intentionally removed pixels,
not an emulator rendering error.

- `current/` retains only the interface regions needed by control matching.
- `friends/` retains crop state, game feedback and action controls. Friend names
  in the social panel are synthetic: ALPHA, BRAVO, CHARLIE, DELTA and ECHO.
- `friends/masking.json` records the permitted pixel rectangles. Tests reject
  pixels outside those rectangles and protected header/avatar/chat regions.
- `fox_crop_name_header.png` contains only a crop-name label.

Do not add a full account screenshot here. Store original captures under the
ignored `tmp/` directory, remove account details, and rerun the recognition and
privacy tests before committing a fixture.
