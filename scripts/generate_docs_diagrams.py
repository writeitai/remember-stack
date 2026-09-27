"""Generate the animated SVG diagrams for the docs site in the remember.dev visual language.

Run: python3 scripts/generate_docs_diagrams.py. search-vs-memory.svg is hand-written;
pipeline-stages.svg and architecture.svg were rendered once with mermaid-cli.
"""

from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "website" / "public" / "docs" / "diagrams"
CYCLE = 14  # seconds per loop


def head(
    *,
    title: str,
    desc: str,
    width: int = 1200,
    height: int = 640,
    steps: int = 10,
    extra_css: str = "",
) -> str:
    """Return the opening of an SVG with shared palette, type and staged reveal classes."""
    step_css = []
    for i in range(1, steps + 1):
        start = 6 + (i - 1) * 6
        step_css.append(
            f".s{i} {{ animation: s{i} {CYCLE}s ease-out infinite both; }}\n"
            f"@keyframes s{i} {{ 0%, {start}% {{ opacity: 0; transform: translateY(8px); }} "
            f"{start + 5}%, 90% {{ opacity: 1; transform: none; }} 97%, 100% {{ opacity: 0; }} }}"
        )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-labelledby="t d">
  <title id="t">{title}</title>
  <desc id="d">{desc}</desc>
  <defs>
    <radialGradient id="glowC" cx="25%" cy="45%" r="45%"><stop offset="0" stop-color="#ee5b44" stop-opacity=".14"/><stop offset="1" stop-color="#ee5b44" stop-opacity="0"/></radialGradient>
    <radialGradient id="glowT" cx="75%" cy="50%" r="45%"><stop offset="0" stop-color="#3e9b8e" stop-opacity=".18"/><stop offset="1" stop-color="#3e9b8e" stop-opacity="0"/></radialGradient>
    <radialGradient id="sphereT" cx="35%" cy="30%" r="70%"><stop offset="0" stop-color="#b9f0e6"/><stop offset=".35" stop-color="#5cc8b6"/><stop offset="1" stop-color="#2a6f66"/></radialGradient>
    <radialGradient id="sphereC" cx="35%" cy="30%" r="70%"><stop offset="0" stop-color="#ffc2b5"/><stop offset=".35" stop-color="#ff7a62"/><stop offset="1" stop-color="#a8392a"/></radialGradient>
    <radialGradient id="sphereS" cx="35%" cy="30%" r="70%"><stop offset="0" stop-color="#f3e6de"/><stop offset=".4" stop-color="#c9b2a6"/><stop offset="1" stop-color="#7d665c"/></radialGradient>
    <marker id="arrT" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="#5cc8b6"/></marker>
    <marker id="arrC" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="#ff7a62"/></marker>
    <marker id="arrM" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="#66739c"/></marker>
    <style>
      text {{ font-family: Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }}
      .h {{ fill: #ffffff; font-size: 22px; font-weight: 700; letter-spacing: -.01em; }}
      .h2 {{ fill: #ffffff; font-size: 17px; font-weight: 700; }}
      .k {{ fill: #66739c; font-size: 12px; font-weight: 600; letter-spacing: .16em; }}
      .b {{ fill: #d9def0; font-size: 15px; }}
      .m {{ fill: #9faaca; font-size: 13px; }}
      .sm {{ fill: #66739c; font-size: 12px; }}
      .w {{ fill: #ffffff; }}
      .tc {{ fill: #5cc8b6; }}
      .cc {{ fill: #ff7a62; }}
      .mono {{ font-family: ui-monospace, "SF Mono", Menlo, monospace; }}
      .card {{ fill: #101c42; stroke: #25335f; }}
      .cardT {{ fill: #0f2a3a; stroke: #3e9b8e; stroke-opacity: .7; }}
      .flow {{ stroke-dasharray: 5 7; animation: flow 1.6s linear infinite; }}
      @keyframes flow {{ to {{ stroke-dashoffset: -24; }} }}
      .pulse {{ transform-box: fill-box; transform-origin: center; animation: pulse 3s ease-in-out infinite; }}
      @keyframes pulse {{ 0%, 100% {{ transform: scale(1); }} 50% {{ transform: scale(1.12); }} }}
      {chr(10).join(step_css)}
      {extra_css}
      @media (prefers-reduced-motion: reduce) {{ * {{ animation: none !important; }} .rm-hide {{ display: none; }} }}
    </style>
  </defs>
  <rect width="{width}" height="{height}" rx="24" fill="#0a1230"/>
  <rect width="{width}" height="{height}" rx="24" fill="url(#glowC)"/>
  <rect width="{width}" height="{height}" rx="24" fill="url(#glowT)"/>
'''


def esc(text: str) -> str:
    """Escape text for SVG."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def claims_and_facts() -> str:
    """Claims (what sources said) feeding facts (what is held true)."""
    s = head(
        title="Claims and facts",
        desc="Three sources make claims about Ravi: kickoff notes on 15 January, slides in February and a retro on 12 June. The first two support the fact that Ravi works on the billing migration, from 14 January. The retro supports a new fact, Ravi works on the search team from 1 June, and closes the earlier fact on 1 June. The claims never change; the facts change and keep their evidence.",
        steps=9,
    )
    s += """  <text class="k" x="60" y="70">WHAT SOURCES SAID</text>
  <text class="h" x="60" y="100">Claims</text>
  <text class="m" x="60" y="124">Never edited. Each keeps its passage and date.</text>
  <text class="k" x="700" y="70">WHAT IS HELD TRUE</text>
  <text class="h" x="700" y="100">Facts</text>
  <text class="m" x="700" y="124">Updated as claims arrive. Each keeps its evidence.</text>
"""
    claims = [
        (
            "Kickoff notes · 15 Jan",
            "“Ravi has been on the billing",
            "migration since yesterday.”",
            160,
        ),
        ("Slides · Feb", "“Ravi is working on the billing", "migration.”", 290),
        ("Retro · 12 Jun", "“Ravi moved to the search team", "on 1 June.”", 420),
    ]
    for i, (meta, l1, l2, y) in enumerate(claims, 1):
        s += f'''  <g class="s{i}">
    <rect class="card" x="60" y="{y}" width="440" height="104" rx="14"/>
    <rect x="60" y="{y}" width="6" height="104" rx="3" fill="#ee5b44"/>
    <circle cx="94" cy="{y + 30}" r="9" fill="url(#sphereC)"/>
    <text class="m" x="114" y="{y + 35}">{esc(meta)}</text>
    <text class="b" x="86" y="{y + 66}">{esc(l1)}</text>
    <text class="b" x="86" y="{y + 88}">{esc(l2)}</text>
  </g>
'''
    # facts
    s += """  <g class="s5">
    <rect class="cardT" x="700" y="175" width="440" height="150" rx="16"/>
    <circle cx="736" cy="212" r="14" fill="url(#sphereT)"/>
    <text class="h2" x="762" y="218">Ravi works on the billing migration</text>
    <text class="m" x="762" y="246">valid 14 Jan – 1 Jun</text>
    <text class="m" x="762" y="270">supported by 2 documents</text>
    <rect x="762" y="284" width="112" height="24" rx="12" fill="#18244f" stroke="#25335f"/>
    <text class="sm" x="776" y="301">now: history</text>
  </g>
  <g class="s7">
    <rect class="cardT" x="700" y="380" width="440" height="120" rx="16"/>
    <circle cx="736" cy="417" r="14" fill="url(#sphereT)"/>
    <text class="h2" x="762" y="423">Ravi works on the search team</text>
    <text class="m" x="762" y="451">valid from 1 Jun</text>
    <text class="m" x="762" y="475">supported by 1 document</text>
  </g>
  <g class="s4">
    <path class="flow" d="M500 212 C 600 212, 600 222, 696 222" fill="none" stroke="#ff7a62" stroke-width="2" marker-end="url(#arrC)"/>
    <path class="flow" d="M500 342 C 600 342, 600 250, 696 246" fill="none" stroke="#ff7a62" stroke-width="2" marker-end="url(#arrC)"/>
  </g>
  <g class="s6">
    <path class="flow" d="M500 472 C 600 472, 600 432, 696 432" fill="none" stroke="#ff7a62" stroke-width="2" marker-end="url(#arrC)"/>
    <path class="flow" d="M500 460 C 610 440, 620 300, 696 280" fill="none" stroke="#5cc8b6" stroke-width="2" marker-end="url(#arrT)"/>
    <text class="sm tc" x="636" y="352">closes on 1 Jun</text>
  </g>
  <g class="s8">
    <text class="b" x="60" y="585"><tspan class="cc">Claims are the transcript;</tspan><tspan class="tc"> facts are the verdict.</tspan></text>
    <text class="m" x="60" y="610">The retro did not erase Ravi's billing work. It gave it an end date, and the history stays queryable.</text>
  </g>
</svg>
"""
    return s


def three_clocks() -> str:
    """World time, said-on time and belief time on one fact, with a query cursor."""
    x0, x1 = 170, 1130  # Jan 1 .. Jul 31 (212 days)

    def x(day: int) -> float:
        return x0 + (x1 - x0) * day / 212

    jan14, jan15, mar1, jun1, jun12, jul1 = 13, 14, 59, 151, 162, 181
    months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL"]
    starts = [0, 31, 59, 90, 120, 151, 181]
    cursor_css = f"""
      .cursor {{ animation: cursor {CYCLE}s ease-in-out infinite both; }}
      @keyframes cursor {{ 0%, 44% {{ transform: translateX(0); opacity: 0; }} 48% {{ opacity: 1; transform: translateX(0); }} 62% {{ transform: translateX(0); opacity: 1; }} 72%, 90% {{ transform: translateX({x(jul1) - x(mar1):.1f}px); opacity: 1; }} 97%, 100% {{ opacity: 0; transform: translateX({x(jul1) - x(mar1):.1f}px); }} }}
      .ansA {{ animation: ansA {CYCLE}s ease-out infinite both; }}
      @keyframes ansA {{ 0%, 50% {{ opacity: 0; }} 54%, 62% {{ opacity: 1; }} 66%, 100% {{ opacity: 0; }} }}
      .ansB {{ animation: ansB {CYCLE}s ease-out infinite both; }}
      @keyframes ansB {{ 0%, 72% {{ opacity: 0; }} 76%, 90% {{ opacity: 1; }} 97%, 100% {{ opacity: 0; }} }}
"""
    s = head(
        title="Three clocks",
        desc="One fact, Ravi works on the billing migration, on three timelines. World time: true from 14 January to 1 June. Said-on time: the kickoff notes said it on 15 January and the retro said it ended on 12 June. Belief time: the memory learned it on 15 January and still holds it. A query at 1 March finds the fact true; a query at 1 July finds it no longer current, ended 1 June.",
        height=600,
        steps=8,
        extra_css=cursor_css,
    )
    s += """  <text class="k" x="60" y="62">ONE FACT, THREE CLOCKS</text>
  <text class="h" x="60" y="92">Ravi works on the billing migration</text>
"""
    for m, d in zip(months, starts, strict=True):
        s += f'  <text class="sm" x="{x(d) + 4:.0f}" y="136">{m}</text>\n  <line x1="{x(d):.0f}" y1="146" x2="{x(d):.0f}" y2="470" stroke="#18244f"/>\n'
    lanes = [
        ("WORLD TIME", "When was it true?", 200),
        ("SAID-ON TIME", "When did a source say it?", 300),
        ("BELIEF TIME", "When did the memory learn it?", 400),
    ]
    for i, (k, q, y) in enumerate(lanes, 1):
        s += f'''  <g class="s{i}">
    <text class="k" x="60" y="{y - 4}" style="font-size:11px">{k}</text>
    <text class="sm" x="60" y="{y + 14}">{q}</text>
    <line x1="{x0}" y1="{y + 30}" x2="{x1}" y2="{y + 30}" stroke="#25335f" stroke-width="2"/>
  </g>
'''
    s += f'''  <g class="s4">
    <rect x="{x(jan14):.1f}" y="218" width="{x(jun1) - x(jan14):.1f}" height="24" rx="12" fill="#3e9b8e"/>
    <text class="m w" x="{x(jan14) + 14:.1f}" y="235">valid 14 Jan – 1 Jun</text>
  </g>
  <g class="s5">
    <circle cx="{x(jan15):.1f}" cy="330" r="10" fill="url(#sphereC)"/>
    <text class="m" x="{x(jan15) - 10:.1f}" y="362">kickoff notes: “since yesterday”</text>
    <circle cx="{x(jun12):.1f}" cy="330" r="10" fill="url(#sphereC)"/>
    <text class="m" x="{x(jun12) - 18:.1f}" y="362" text-anchor="middle">retro: “moved on 1 June”</text>
    <path d="M{x(jan15):.1f} 318 L{x(jan14):.1f} 248" stroke="#ff7a62" stroke-dasharray="3 4" marker-end="url(#arrC)"/>
    <path d="M{x(jun12):.1f} 318 L{x(jun1) + 2:.1f} 248" stroke="#ff7a62" stroke-dasharray="3 4" marker-end="url(#arrC)"/>
  </g>
  <g class="s6">
    <path d="M{x(jan15):.1f} 430 L{x1:.1f} 430" stroke="#5cc8b6" stroke-width="4" stroke-linecap="round"/>
    <circle cx="{x(jan15):.1f}" cy="430" r="8" fill="url(#sphereT)"/>
    <text class="m" x="{x(jan15) + 16:.1f}" y="458">learned 15 Jan · still believed</text>
  </g>
  <g class="cursor">
    <line x1="{x(mar1):.1f}" y1="150" x2="{x(mar1):.1f}" y2="470" stroke="#ffffff" stroke-width="2" stroke-dasharray="2 5"/>
    <circle cx="{x(mar1):.1f}" cy="150" r="5" fill="#ffffff"/>
  </g>
  <g class="ansA">
    <rect x="60" y="500" width="620" height="60" rx="14" class="card"/>
    <text class="b mono" x="84" y="536">time: at 1 March</text>
    <text class="b tc" x="300" y="536">→ true, from the kickoff notes</text>
  </g>
  <g class="ansB rm-hide">
    <rect x="60" y="500" width="620" height="60" rx="14" class="card"/>
    <text class="b mono" x="84" y="536">time: at 1 July</text>
    <text class="b cc" x="300" y="536">→ not current: ended 1 June</text>
  </g>
</svg>
'''
    return s


def pipeline() -> str:
    """The stages a document passes through before it is queryable."""
    css = f"""
      .packet {{ animation: packet {CYCLE}s linear infinite both; }}
      @keyframes packet {{ 0%, 4% {{ offset-distance: 0%; opacity: 0; }} 6% {{ opacity: 1; }} 86% {{ offset-distance: 100%; opacity: 1; }} 90%, 100% {{ offset-distance: 100%; opacity: 0; }} }}
"""
    s = head(
        title="The pipeline",
        desc="A document passes through stages before it can be queried. Reading: store, convert to Markdown, find sections, cut passages, select statements worth keeping and turn them into claims, check each claim against the source. Connecting: resolve names to entities, turn claims into facts, weigh each against existing facts, and index. Then it is queryable. Nothing on the answering side calls a language model.",
        height=560,
        steps=10,
        extra_css=css,
    )
    row1 = [
        ("Store", "raw file kept"),
        ("Convert", "to Markdown"),
        ("Structure", "find sections"),
        ("Chunk", "cut passages"),
        ("Extract", "select, rewrite"),
        ("Ground", "check vs source"),
    ]
    row2 = [
        ("Resolve", "names → entities"),
        ("Form facts", "relations, observations"),
        ("Adjudicate", "confirm, adjust, close"),
        ("Reconcile", "recount support"),
        ("Index", "search, graph"),
        ("Queryable", "readiness: ready"),
    ]
    s += """  <text class="k" x="60" y="62">FROM FILE TO MEMORY</text>
  <text class="h" x="60" y="92">What happens after you send a document</text>
  <text class="k cc" x="60" y="146" style="fill:#ff7a62">READ</text>
  <text class="k tc" x="60" y="336" style="fill:#5cc8b6">CONNECT</text>
"""
    xs = [60 + i * 184 for i in range(6)]
    path = f"M{xs[0] + 80} 210 L{xs[5] + 80} 210 C {xs[5] + 170} 210, {xs[5] + 170} 400, {xs[5] + 80} 400 L{xs[0] + 80} 400"
    s += f'  <path d="{path}" fill="none" stroke="#25335f" stroke-width="2"/>\n'
    s += f'  <path class="flow" d="{path}" fill="none" stroke="#66739c" stroke-width="2" stroke-opacity=".6"/>\n'
    for i, (name, sub) in enumerate(row1):
        s += f'''  <g class="s{i + 1 if i < 5 else 5}">
    <rect class="card" x="{xs[i]}" y="170" width="160" height="80" rx="14"/>
    <circle cx="{xs[i] + 24}" cy="198" r="8" fill="url(#sphereC)"/>
    <text class="h2" x="{xs[i] + 40}" y="204" style="font-size:15px">{name}</text>
    <text class="sm" x="{xs[i] + 18}" y="232">{sub}</text>
  </g>
'''
    for i, (name, sub) in enumerate(row2):
        xx = xs[5 - i]
        final = name == "Queryable"
        s += f'''  <g class="s{6 + i // 2}">
    <rect class="{"cardT" if final else "card"}" x="{xx}" y="360" width="160" height="80" rx="14"/>
    <circle cx="{xx + 24}" cy="388" r="8" fill="url(#{"sphereT" if not final else "sphereT"})"/>
    <text class="h2" x="{xx + 40}" y="394" style="font-size:15px">{name}</text>
    <text class="sm" x="{xx + 18}" y="422">{sub}</text>
  </g>
'''
    # row 2 reads right-to-left along the path, so reverse visually: place in reverse order
    s = s.replace("<!--rev-->", "")
    s += f"""  <circle class="packet rm-hide" r="7" fill="#ffffff" style="offset-path: path('{path}')"/>
  <g class="s9">
    <text class="m" x="60" y="492">Each stage runs as its own worker and records its result. Processing takes minutes, not milliseconds.</text>
    <text class="m" x="60" y="516">Answering a question later runs none of this: it reads what the pipeline already wrote.</text>
  </g>
</svg>
"""
    return s


def read_path() -> str:
    """Nominate, confirm, account: how a question becomes a result."""
    s = head(
        title="How a question is answered",
        desc="A question goes to several search channels at once: meaning (vector search), keywords, and the entity graph. They nominate candidates. PostgreSQL then confirms every candidate against what is currently held, in one consistent snapshot, and drops anything withdrawn or superseded. The result lists the facts, their evidence and time windows, and says how many candidates were dropped. A stale index can cost recall but never serves a withdrawn fact.",
        height=600,
        steps=9,
    )
    s += """  <text class="k" x="60" y="62">THE READ PATH</text>
  <text class="h" x="60" y="92">Indexes nominate. The database confirms. The result accounts.</text>
  <g class="s1">
    <rect x="60" y="250" width="200" height="90" rx="16" fill="#18244f" stroke="#25335f"/>
    <circle class="pulse" cx="88" cy="280" r="8" fill="#ff7a62"/>
    <text class="h2" x="104" y="286" style="font-size:15px">Question</text>
    <text class="sm" x="80" y="316">“Who owns the invoice</text>
    <text class="sm" x="80" y="332">exporter?”</text>
  </g>
"""
    chans = [
        ("Meaning", "vector search", 150),
        ("Keywords", "BM25", 265),
        ("Graph", "neighbours of entities", 380),
    ]
    for n, sub, y in chans:
        s += f'''  <g class="s2">
    <path class="flow" d="M260 295 C 300 295, 300 {y + 35}, 336 {y + 35}" fill="none" stroke="#66739c" stroke-width="1.5" marker-end="url(#arrM)"/>
    <rect class="card" x="340" y="{y}" width="200" height="70" rx="14"/>
    <text class="h2" x="362" y="{y + 30}" style="font-size:15px">{n}</text>
    <text class="sm" x="362" y="{y + 52}">{sub}</text>
  </g>
'''
    s += """  <g class="s3">
    <text class="k" x="340" y="136" style="font-size:11px">1 · NOMINATE</text>
"""
    for j in range(9):
        cx = 580 + (j % 3) * 26
        cy = 245 + (j // 3) * 36
        fill = "url(#sphereS)" if j in (2, 7) else "url(#sphereT)"
        s += f'    <circle cx="{cx}" cy="{cy}" r="9" fill="{fill}"/>\n'
    s += """    <text class="sm" x="566" y="380">candidates</text>
  </g>
  <g class="s4">
    <path class="flow" d="M660 290 L 716 290" stroke="#66739c" stroke-width="1.5" marker-end="url(#arrM)"/>
    <text class="k" x="720" y="136" style="font-size:11px">2 · CONFIRM</text>
    <rect x="720" y="200" width="200" height="180" rx="16" fill="#0f2a3a" stroke="#3e9b8e" stroke-opacity=".7"/>
    <text class="h2" x="742" y="232" style="font-size:15px">PostgreSQL</text>
    <text class="sm" x="742" y="256">one snapshot, live state</text>
    <text class="sm" x="742" y="290">✓ still held</text>
    <text class="sm" x="742" y="312">✓ current evidence</text>
    <text class="sm" x="742" y="334" style="fill:#ff7a62">✕ withdrawn: dropped</text>
    <text class="sm" x="742" y="356" style="fill:#ff7a62">✕ superseded: dropped</text>
  </g>
  <g class="s6">
    <path class="flow" d="M920 290 L 976 290" stroke="#5cc8b6" stroke-width="1.5" marker-end="url(#arrT)"/>
    <text class="k" x="980" y="136" style="font-size:11px">3 · ACCOUNT</text>
    <rect class="card" x="980" y="200" width="170" height="180" rx="16"/>
    <text class="h2" x="1000" y="232" style="font-size:15px">Result</text>
    <circle cx="1008" cy="262" r="7" fill="url(#sphereT)"/>
    <text class="sm" x="1022" y="266">facts + evidence</text>
    <circle cx="1008" cy="288" r="7" fill="url(#sphereT)"/>
    <text class="sm" x="1022" y="292">time windows</text>
    <circle cx="1008" cy="314" r="7" fill="url(#sphereT)"/>
    <text class="sm" x="1022" y="318">contradictions</text>
    <text class="sm mono" x="1000" y="352" style="fill:#ff7a62">dropped: 2</text>
  </g>
  <g class="s8">
    <text class="b" x="60" y="506">A stale index can cost recall. It can never serve a fact the memory no longer holds.</text>
    <text class="m" x="60" y="534">No language model writes the answer. The question is only embedded for the meaning channel.</text>
    <text class="m" x="60" y="558">The result says what was dropped, so an agent knows when it is not seeing everything.</text>
  </g>
</svg>
"""
    return s


def provenance() -> str:
    """From a fact to the characters in the source document."""
    s = head(
        title="From a fact to its source",
        desc="Every fact links to the claims that support it. Every claim links to a passage and to its exact character positions in a document version. Follow the chain from the fact Dana leads the billing migration to the retro claim, to the passage in the June retro, to the highlighted sentence in the original file.",
        height=520,
        steps=8,
    )
    boxes = [
        (
            "FACT",
            "Dana leads the billing",
            "migration · since 1 Jun",
            60,
            "cardT",
            "sphereT",
        ),
        ("CLAIM", "“Dana now leads the", "billing migration.”", 340, "card", "sphereC"),
        ("PASSAGE", "Retro notes, section", "“Team changes”", 620, "card", "sphereS"),
        (
            "DOCUMENT",
            "retro-2026-06-12.md",
            "version 1 · chars 412–455",
            900,
            "card",
            "sphereS",
        ),
    ]
    s += """  <text class="k" x="60" y="62">PROVENANCE</text>
  <text class="h" x="60" y="92">Every answer has a path back to the sentence</text>
"""
    for i, (k, l1, l2, xx, cls, sph) in enumerate(boxes, 1):
        s += f'''  <g class="s{i * 2 - 1}">
    <rect class="{cls}" x="{xx}" y="150" width="240" height="120" rx="16"/>
    <circle cx="{xx + 26}" cy="178" r="9" fill="url(#{sph})"/>
    <text class="k" x="{xx + 44}" y="183" style="font-size:11px">{k}</text>
    <text class="b" x="{xx + 22}" y="220">{esc(l1)}</text>
    <text class="m" x="{xx + 22}" y="244">{esc(l2)}</text>
  </g>
'''
        if i < 4:
            s += f'  <g class="s{i * 2}"><path class="flow" d="M{xx + 240} 210 L{xx + 276} 210" stroke="#66739c" stroke-width="2" marker-end="url(#arrM)"/></g>\n'
    s += """  <g class="s7">
    <rect class="card" x="60" y="320" width="1080" height="96" rx="16"/>
    <text class="sm mono" x="84" y="354">## Team changes</text>
    <text class="b mono" x="84" y="386" style="font-size:14px">Ravi moved to the search team on 1 June.</text>
    <text class="b mono" x="761" y="386" style="font-size:14px">Launch stays in October.</text>
    <rect x="435" y="368" width="324" height="26" rx="6" fill="#5cc8b6" fill-opacity=".9"/>
    <text class="b mono" x="441" y="386" style="font-size:14px;fill:#0a1230">Dana now leads the billing migration.</text>
  </g>
  <g class="s8">
    <text class="m" x="60" y="462">An agent can quote the sentence word for word. A person can open the file and find it.</text>
  </g>
</svg>
"""
    return s


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in [
        ("claims-and-facts", claims_and_facts),
        ("three-clocks", three_clocks),
        ("pipeline", pipeline),
        ("read-path", read_path),
        ("provenance", provenance),
    ]:
        (OUT / f"{name}.svg").write_text(fn())
        print("wrote", name)
