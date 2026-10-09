"""与浅色页面一致的Plotly样式。"""

MINT = "#187C68"
AMBER = "#A36B22"
RED = "#BD4C57"
INK = "#192D3E"
GRID = "#E5EBF0"


def style_chart(figure, height=320):
    figure.update_layout(
        template="plotly_white",
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Bahnschrift, Microsoft YaHei, sans-serif", color=INK, size=12),
        margin=dict(l=10, r=15, t=20, b=15),
        colorway=[MINT, AMBER, RED],
        legend=dict(orientation="h", yanchor="bottom", y=1.03, x=0),
    )
    figure.update_xaxes(gridcolor=GRID, zerolinecolor=GRID)
    figure.update_yaxes(gridcolor=GRID, zerolinecolor=GRID)
    return figure
