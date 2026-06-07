import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle, FancyArrowPatch
import matplotlib as mpl
from pathlib import Path

mpl.rcParams['font.family'] = 'DejaVu Sans'
mpl.rcParams['pdf.fonttype'] = 42
mpl.rcParams['ps.fonttype'] = 42
mpl.rcParams['svg.fonttype'] = 'none'

COL_PANEL = '#111111'
COL_LANE_BG = '#f8f8f8'
COL_DURABLE = '#d8d8d8'
COL_GRID = '#c9c9c9'
COL_TEXT = '#111111'
COL_MUTED = '#555555'
COL_WARN = '#b24b3a'
COL_OK = '#2f6f4e'
COL_OK_BG = '#f3fbf6'
COL_BOX = '#ffffff'

# Two-panel layout.  Larger effective text size when included at \linewidth.
fig = plt.figure(figsize=(8.2, 3.85))
ax = fig.add_axes([0, 0, 1, 1])
ax.set_axis_off(); ax.set_xlim(0, 1); ax.set_ylim(0, 1)

panel_y = 0.125
panel_h = 0.835
panel_w = 0.435
panel_gap = 0.050
panel_xs = [0.040, 0.040 + panel_w + panel_gap]

def P(x0, y0, w, h, xp, yp):
    return x0 + xp * w, y0 + yp * h

def add_round(x, y, w, h, r=0.02, fc='white', ec=COL_PANEL, lw=1.0, z=1, pad=0.002, ls='-'):
    p = FancyBboxPatch((x, y), w, h,
                       boxstyle=f"round,pad={pad},rounding_size={r}",
                       linewidth=lw, edgecolor=ec, facecolor=fc,
                       linestyle=ls, zorder=z)
    ax.add_patch(p)
    return p

def draw_panel(x, y, w, h, title, subtitle, highlight=False):
    if highlight:
        add_round(x, y, w, h, r=0.020, fc=COL_OK_BG, ec=COL_OK, lw=2.6, z=0)
        title_color = COL_OK
    else:
        add_round(x, y, w, h, r=0.018, fc='white', ec=COL_PANEL, lw=1.25, z=0)
        title_color = COL_TEXT
    ax.text(x + w/2, y + h - 0.043, title,
            ha='center', va='top', fontsize=13.0, fontweight='semibold', color=title_color)
    # Lower subtitle line enough to avoid cramped two-line titles.
    ax.text(x + w/2, y + h - 0.112, subtitle,
            ha='center', va='top', fontsize=10.0, color=COL_MUTED, linespacing=1.18)

def draw_lanes(x, y, w, h, durable_points, slow_idx=None):
    # Move lanes to the right so shard labels never collide with lane boxes.
    lane_x = 0.315
    lane_w = 0.585
    lane_h = 0.064
    row_y = [0.710, 0.625, 0.540, 0.455]
    for i, yp in enumerate(row_y):
        ax.text(*P(x, y, w, h, 0.050, yp + lane_h/2), f'WAL shard {i}',
                ha='left', va='center', fontsize=8.6, color=COL_TEXT)
        X, Y = P(x, y, w, h, lane_x, yp); W, H = w * lane_w, h * lane_h
        outer = add_round(X, Y, W, H, r=0.005, fc=COL_LANE_BG, ec='#333333', lw=0.90, z=1, pad=0.001)
        dp = durable_points[i]
        rect = Rectangle((X, Y), W * dp, H, linewidth=0, facecolor=COL_DURABLE, zorder=1.2)
        rect.set_clip_path(outer); ax.add_patch(rect)
        for tx in [0.15, 0.37, 0.59, 0.80]:
            Xt, Yb = P(x, y, w, h, lane_x + lane_w * tx, yp + 0.007)
            _, Yt = P(x, y, w, h, 0, yp + lane_h - 0.007)
            ax.plot([Xt, Xt], [Yb, Yt], color=COL_GRID, lw=0.9, zorder=1.5)
        Xdp, Yb = P(x, y, w, h, lane_x + lane_w * dp, yp - 0.014)
        _, Yt = P(x, y, w, h, 0, yp + lane_h + 0.014)
        ax.plot([Xdp, Xdp], [Yb, Yt], color=COL_PANEL, lw=1.10, zorder=3)
        if slow_idx is not None and i == slow_idx:
            ax.text(*P(x, y, w, h, 0.875, yp + lane_h/2), 'slow',
                    ha='right', va='center', fontsize=8.0, color=COL_MUTED)
    return lane_x, lane_w, lane_h, row_y

def record(x, y, w, h, lane_x, lane_w, rows, row, xpos, label, edge=COL_PANEL, lw=1.0, fc=COL_BOX):
    rec_w, rec_h = 0.090, 0.046
    px = lane_x + lane_w * xpos - rec_w/2
    py = rows[row] + 0.064/2 - rec_h/2
    X, Y = P(x, y, w, h, px, py); W, H = w * rec_w, h * rec_h
    add_round(X, Y, W, H, r=0.004, fc=fc, ec=edge, lw=lw, z=5, pad=0.001)
    ax.text(X + W/2, Y + H/2, label, ha='center', va='center', fontsize=10.2, color=COL_TEXT, zorder=6)
    return X + W/2, Y + H/2

def arrow(start, end, color=COL_PANEL, lw=1.0, rad=0.0, ms=9):
    a = FancyArrowPatch(start, end, arrowstyle='->', mutation_scale=ms,
                        linewidth=lw, color=color, connectionstyle=f'arc3,rad={rad}', zorder=4,
                        shrinkA=0, shrinkB=0)
    ax.add_patch(a); return a

def dep_arrow(U, T, rec_w_fig, rec_h_fig):
    # Connect from U's right edge to T's left edge.  The endpoint lies on
    # T's border, so the arrow visibly attaches to the T record.
    start = (U[0] + rec_w_fig/2, U[1])
    end = (T[0] - rec_w_fig/2, T[1])
    return arrow(start, end, color=COL_PANEL, lw=1.15, rad=0.0, ms=10)

def callout(x, y, w, h, xp, yp, text, ec=COL_PANEL, fc='#fbfbfb', fontsize=9.6):
    cw, ch = 0.66, 0.098
    X, Y = P(x, y, w, h, xp - cw/2, yp - ch/2)
    add_round(X, Y, w*cw, h*ch, r=0.011, fc=fc, ec=ec, lw=0.95, z=2, pad=0.004)
    ax.text(X + w*cw/2, Y + h*ch/2, text,
            ha='center', va='center', fontsize=fontsize, color=COL_TEXT, linespacing=1.18, zorder=3)
    return X + w*cw/2, Y + h*ch/2

def bottom_note(x, y, w, h, text, color=COL_TEXT, fontsize=9.8):
    ax.text(*P(x, y, w, h, 0.5, 0.142), text,
            ha='center', va='center', fontsize=fontsize, color=color, linespacing=1.22)

# (a) Global durable prefix.
x, y, w, h = panel_xs[0], panel_y, panel_w, panel_h
draw_panel(x, y, w, h, '(a) Global durable prefix', 'Safe, but waits for unrelated slow shards')
lx, lw, lh, rows = draw_lanes(x, y, w, h, [0.80, 0.80, 0.38, 0.80], slow_idx=2)
Xg, Yb = P(x, y, w, h, lx + lw*0.38, 0.438); _, Yt = P(x, y, w, h, 0, 0.760)
ax.plot([Xg, Xg], [Yb, Yt], color=COL_PANEL, lw=1.1, ls=(0, (3, 2)), zorder=4)
ax.text(Xg, Yb - 0.014, 'global durable\nprefix', ha='center', va='top',
        fontsize=8.1, color=COL_TEXT, linespacing=0.95)
U = record(x, y, w, h, lx, lw, rows, 0, 0.31, 'U', edge=COL_PANEL, lw=1.1)
T = record(x, y, w, h, lx, lw, rows, 1, 0.62, 'T', edge=COL_WARN, lw=1.4)
dep_arrow(U, T, w*0.090, h*0.046)
ax.text(T[0], T[1] + h*0.073, 'dependency', ha='center', va='bottom',
        fontsize=8.3, color=COL_MUTED, zorder=7)
callout(x, y, w, h, 0.50, 0.285, 'U and T are durable,\nbut T waits for slow shard', ec=COL_WARN)
bottom_note(x, y, w, h, 'One slow shard holds back\ntransactions whose dependencies are durable')

# (b) Proposed TideWAL.
x, y, w, h = panel_xs[1], panel_y, panel_w, panel_h
draw_panel(x, y, w, h, '(b) Proposed method: TideWAL', 'Safe & selective: wait only for dependencies', highlight=True)
lx, lw, lh, rows = draw_lanes(x, y, w, h, [0.50, 0.75, 0.38, 0.75], slow_idx=2)
U = record(x, y, w, h, lx, lw, rows, 0, 0.38, 'U', edge=COL_OK, lw=1.6)
T = record(x, y, w, h, lx, lw, rows, 1, 0.65, 'T', edge=COL_OK, lw=1.6)
dep_arrow(U, T, w*0.090, h*0.046)
ax.text(T[0], T[1] + h*0.073, 'dependency', ha='center', va='bottom',
        fontsize=8.3, color=COL_MUTED, zorder=7)
# dependency frontier bracket
X1, Y1 = P(x, y, w, h, lx + lw*0.50, 0.430); X2, _ = P(x, y, w, h, lx + lw*0.75, 0.430)
ax.plot([X1, X2], [Y1, Y1], color=COL_OK, lw=2.0, zorder=4)
ax.plot([X1, X1], [Y1-0.012, Y1+0.012], color=COL_OK, lw=2.0, zorder=4)
ax.plot([X2, X2], [Y1-0.012, Y1+0.012], color=COL_OK, lw=2.0, zorder=4)
ax.text((X1 + X2)/2, Y1 - 0.017, 'dependency frontier', ha='center', va='top',
        fontsize=8.3, color=COL_OK, fontweight='semibold')
callout(x, y, w, h, 0.50, 0.285, 'Ack T after T and\nits dependencies are durable', ec=COL_OK, fc='white')
bottom_note(x, y, w, h, 'No global-prefix wait\nfor unrelated slow shards', color=COL_OK)

# Legend: larger and simplified.
leg_x, leg_y, leg_w, leg_h = 0.065, 0.028, 0.870, 0.064
add_round(leg_x, leg_y, leg_w, leg_h, r=0.008, fc='white', ec='#bdbdbd', lw=0.7, z=10)
cy = leg_y + leg_h/2
items = [
    (leg_x + 0.035, 'durable region', 'region'),
    (leg_x + 0.255, 'durable point', 'point'),
    (leg_x + 0.470, 'global prefix', 'prefix'),
    (leg_x + 0.675, 'log record', 'record'),
]
for x0, label, kind in items:
    if kind == 'region':
        ax.add_patch(Rectangle((x0, cy-0.012), 0.030, 0.024, facecolor=COL_DURABLE, edgecolor='#888888', linewidth=0.6, zorder=11))
        ax.text(x0+0.040, cy, label, va='center', ha='left', fontsize=8.1, zorder=11)
    elif kind == 'point':
        ax.plot([x0, x0], [cy-0.014, cy+0.014], color=COL_PANEL, lw=1.1, zorder=11)
        ax.text(x0+0.028, cy, label, va='center', ha='left', fontsize=8.1, zorder=11)
    elif kind == 'prefix':
        ax.plot([x0, x0], [cy-0.014, cy+0.014], color=COL_PANEL, lw=1.1, ls=(0, (3, 2)), zorder=11)
        ax.text(x0+0.028, cy, label, va='center', ha='left', fontsize=8.1, zorder=11)
    elif kind == 'record':
        add_round(x0, cy-0.014, 0.034, 0.028, r=0.002, fc='white', ec=COL_PANEL, lw=0.8, z=11, pad=0.001)
        ax.text(x0+0.046, cy, label, va='center', ha='left', fontsize=8.1, zorder=11)

out = Path(__file__).resolve().with_name('fig1_tidewal_ack_policies')
for ext in ['pdf', 'png', 'svg']:
    if ext == 'png':
        fig.savefig(f'{out}.{ext}', dpi=300, bbox_inches='tight', pad_inches=0.02)
    else:
        fig.savefig(f'{out}.{ext}', bbox_inches='tight', pad_inches=0.02)
print(out)
