"""生成 macOS 风格应用图标（squircle + 渐变 + 下载符号）。

输出:
    assets/icon.png  — 512x512 PNG（网页 / 文档用）
    assets/icon.ico  — 多尺寸 Windows 图标（桌面快捷方式用）

用法:
    .venv/Scripts/python.exe scripts/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 1024
RADIUS_RATIO = 0.225  # macOS squircle 近似圆角

# 渐变起止色（与前端 accent 配色一致）
TOP = (10, 132, 255)  # #0a84ff
BOTTOM = (94, 92, 230)  # #5e5ce6

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"


def _lerp(
    a: tuple[int, int, int],
    b: tuple[int, int, int],
    t: float,
) -> tuple[int, int, int]:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def make_icon() -> None:
    ASSETS.mkdir(exist_ok=True)
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))

    # 垂直渐变底
    grad = Image.new("RGBA", (SIZE, SIZE))
    gd = ImageDraw.Draw(grad)
    for y in range(SIZE):
        gd.line([(0, y), (SIZE, y)], fill=_lerp(TOP, BOTTOM, y / SIZE) + (255,))

    # squircle 蒙版（4 倍超采样抗锯齿）
    ss = 4
    mask = Image.new("L", (SIZE * ss, SIZE * ss), 0)
    md = ImageDraw.Draw(mask)
    r = int(SIZE * ss * RADIUS_RATIO)
    md.rounded_rectangle([0, 0, SIZE * ss - 1, SIZE * ss - 1], radius=r, fill=255)
    mask = mask.resize((SIZE, SIZE), Image.LANCZOS)
    img.paste(grad, (0, 0), mask)

    # L 字母：竖笔 + 横笔，全圆角端头，居中偏上
    d = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)
    sw = int(SIZE * 0.175)  # 笔画宽度
    x0 = int(SIZE * 0.30)  # 竖笔左缘
    top = int(SIZE * 0.21)
    bottom = int(SIZE * 0.79)  # 横笔底缘
    foot_right = int(SIZE * 0.74)  # 横笔右缘

    # 竖笔（上下圆头）
    d.rounded_rectangle(
        [x0, top, x0 + sw, bottom],
        radius=sw // 2,
        fill=white,
    )
    # 横笔（右端圆头）
    d.rounded_rectangle(
        [x0, bottom - sw, foot_right, bottom],
        radius=sw // 2,
        fill=white,
    )

    img.save(ASSETS / "icon.png")
    img.resize((512, 512), Image.LANCZOS).save(ASSETS / "icon_512.png")
    ico_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128)]
    ico_sizes.append((256, 256))
    img.save(ASSETS / "icon.ico", sizes=ico_sizes)
    print(f"图标已生成: {ASSETS / 'icon.png'}, {ASSETS / 'icon.ico'}")


if __name__ == "__main__":
    make_icon()
