# Тема загрузочного меню

`theme.txt` — тема GRUB (gfxmenu) для установочного ISO, `background.png` — фон 1024×768,
`select_*.png` — подсветка выбранного пункта.

Фон рендерится из `background.html` (тот же стиль, что у страницы входа панели):

```bash
NODE_PATH=$(npm root -g) node tests/render_html.mjs image/grub/background.html image/grub/background.png 1024 768
```

Пиксмапы подсветки — однотонные RGBA 6×6 (110, 96, 255, 150).
