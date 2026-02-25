def fmt_pct(ratio):
    if ratio is None:
        return "—"
    return f"{ratio*100:.0f}%"

def fmt_m2(value):
    if value is None:
        return "—"
    return f"{value:,.2f} m²".replace(",", "X").replace(".", ",").replace("X", ".")
