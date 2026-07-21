"""Pure human-readable proof rendering for Connected Change results."""

from __future__ import annotations

from typing import Literal


def render_connected_proof_html(
    receipt_fingerprint: str,
    organized_tree_commitment: str,
    *,
    release_profile: Literal["legacy_name_atlas", "foldweave"] = ("legacy_name_atlas"),
) -> bytes:
    """Render exact portable proof bytes from independently verified identities."""

    if release_profile == "legacy_name_atlas":
        # This branch is byte-for-byte stable because historical receipts commit the
        # portable proof artifact. New visual work belongs only to the Foldweave v3
        # renderer below.
        return (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>Name Atlas proof</title><style>"
            "*{box-sizing:border-box}body{margin:0;background:#0d1117;color:#e6edf3;"
            "font:16px/1.55 system-ui,sans-serif}main{width:min(100%,48rem);"
            "margin:auto;"
            "padding:clamp(1.25rem,5vw,3rem)}h1{font-size:clamp(1.75rem,6vw,2.5rem);"
            "line-height:1.15}details{margin-top:1.5rem;padding:1rem;border:1px solid "
            "#30363d;border-radius:.75rem;background:#161b22}summary{cursor:pointer;"
            "font-weight:700}code{overflow-wrap:anywhere;word-break:break-word;color:#a5d6ff}"
            "</style></head><body><main><h1>Your new folder is verified</h1>"
            "<p>Every in-scope file is present exactly once. The original folder was "
            "not changed.</p><details><summary>Technical proof</summary><p>Receipt: "
            f"<code>{receipt_fingerprint}</code></p><p>Organized tree: <code>"
            f"{organized_tree_commitment}</code></p></details></main></body></html>\n"
        ).encode()

    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="theme-color" content="#f5f5f7" '
        'media="(prefers-color-scheme:light)">'
        '<meta name="theme-color" content="#1c1c1e" '
        'media="(prefers-color-scheme:dark)">'
        "<title>Foldweave proof</title><style>"
        ":root{color-scheme:light dark;--bg:#f5f5f7;--surface:#fff;--text:#1d1d1f;"
        "--muted:#6e6e73;--green:#248a3d}"
        "@media(prefers-color-scheme:dark){:root{--bg:#1c1c1e;--surface:#2c2c2e;"
        "--text:#f5f5f7;"
        "--muted:#aeaeb2;--green:#30d158}}"
        "*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);"
        'font:15px/1.5 -apple-system,BlinkMacSystemFont,system-ui,"Helvetica Neue",'
        "sans-serif;-webkit-font-smoothing:antialiased}header{background:transparent}"
        "header div{width:min(100% - 2rem,46rem);margin:auto;padding:1rem 0 .25rem;"
        "font-size:.875rem;font-weight:600}main{width:min(100% - 2rem,46rem);"
        "margin:0 auto;padding:1.5rem 0 2.5rem}"
        "h1{margin:.45rem 0 .3rem;font-size:clamp(1.55rem,5vw,1.85rem);"
        "line-height:1.2;"
        "letter-spacing:-.018em}.status{display:flex;align-items:center;gap:.4rem;"
        "margin:0;color:var(--text)}.checkmark{color:var(--green);font-weight:700}"
        ".lede{margin:0 0 1.5rem;color:var(--muted)}dl{display:grid;gap:.2rem;"
        "margin:1rem 0;padding:.35rem .9rem;border-radius:.75rem;"
        "background:var(--surface)}dl div{display:grid;grid-template-columns:"
        "minmax(7rem,.7fr) minmax(0,1fr);gap:1rem;padding:.5rem 0}"
        "dt{color:var(--muted);font-size:.75rem}dd{margin:0;font-weight:500}"
        "details{margin-top:1.25rem;padding:.75rem .9rem;border-radius:.75rem;"
        "background:var(--surface)}"
        "summary{cursor:pointer;font-weight:500}"
        "code{overflow-wrap:anywhere;word-break:break-word;color:var(--text);font-family:"
        'ui-monospace,"SFMono-Regular",Menlo,monospace;font-size:.75rem}'
        "</style></head><body><header><div>Foldweave</div></header><main>"
        '<p class="status"><span class="checkmark" aria-hidden="true">&#10003;</span>'
        "<strong>Verified</strong></p><h1>Your new folder is ready</h1>"
        '<p class="lede">Every in-scope file is present exactly once. The selected '
        "source was not changed.</p>"
        "<dl><div><dt>Source</dt><dd>Unchanged</dd></div><div><dt>File accounting</dt>"
        "<dd>Complete</dd></div><div><dt>Verification</dt>"
        "<dd>Passed independently</dd></div>"
        "</dl><details><summary>Technical proof</summary><p>Receipt<br><code>"
        f"{receipt_fingerprint}</code></p><p>Organized tree<br><code>"
        f"{organized_tree_commitment}</code></p></details></main></body></html>\n"
    ).encode()
