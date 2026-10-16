## Bazalth

Dark, monochrome theme for Qt apps: a Kvantum style and a matching KDE color scheme.
Flat, compact widgets in shades of grey, with small rounded corners.

- `Kvantum/Bazalth/`: Kvantum style
- `color-schemes/Bazalth.colors`: KDE color scheme

## Install

```sh
./install.sh            # copy the files to ~/.config/Kvantum and ~/.local/share/color-schemes
./install.sh --apply    # also select the theme in Kvantum, qt5ct, qt6ct and kdeglobals
```

`--apply` changes single keys and keeps a `.bak` of every file it touches.
Without it, select the theme yourself:

- Kvantum: `theme=Bazalth` in `~/.config/Kvantum/kvantum.kvconfig` (or use `kvantummanager`)
- qt5ct / qt6ct: style `kvantum`, color scheme `~/.local/share/color-schemes/Bazalth.colors`

Apps need `QT_QPA_PLATFORMTHEME=qt5ct` (or `qt6ct`) to pick these up outside KDE Plasma.

## Uninstall

```sh
./uninstall.sh          # remove the files
./uninstall.sh --apply  # also restore the configs backed up by install.sh --apply
```

## Customize

Every color, opacity and size lives in `palette.toml`. Change it, then rebuild:

```sh
./build.py   # Python 3.11+, no dependencies
```

`Kvantum/` and `color-schemes/` are generated from `palette.toml` and the files in
`templates/`, and committed so installing needs no build.

## Credits

Based on [Graphite KDE theme](https://github.com/vinceliuice/Graphite-kde-theme) by
vinceliuice, itself based on KvAdapta by Tsu Jan, the author of
[Kvantum](https://github.com/tsujan/Kvantum). The icons (arrows, window buttons, grips)
are Graphite's, recolored.

## License

GNU GPL v3
