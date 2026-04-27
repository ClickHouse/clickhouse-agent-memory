#!/usr/bin/env python3
"""
Headless Playwright audit of the slide deck.

Opens docs/slides/enterprise-agent-memory.html at the Reveal.js target
resolution (1920x1080 — 2x the 960x540 authoring size) and screenshots
each slide by navigating to `#/N`. Also inspects the DOM for common
layout issues: text overflow, elements past slide bounds, big vertical
gaps, content extending into the yellow stripe zone.

Usage:
    python3 benchmarks/harness/screenshot_deck.py \
        --deck docs/slides/enterprise-agent-memory.html \
        --out  benchmarks/results/screenshots/
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

# Tolerate older playwright API surface
from playwright.async_api import async_playwright


async def audit(deck: pathlib.Path, out_dir: pathlib.Path, slides: int) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    findings: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        # 1920x1080 projector resolution; Reveal scales 960x540 up by 2x.
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await context.new_page()

        url = f"file://{deck.resolve()}"
        await page.goto(url)
        await page.wait_for_selector(".reveal .slides")
        # Give Reveal a beat to apply its transforms/fonts.
        await page.wait_for_timeout(800)

        for i in range(1, slides + 1):
            # Navigate to slide i via Reveal's hash API
            await page.evaluate(f"Reveal.slide({i-1})")
            await page.wait_for_timeout(300)

            shot = out_dir / f"slide-{i:02d}.png"
            await page.screenshot(path=str(shot), full_page=False)

            # Inspect the active slide's DOM
            info = await page.evaluate("""
                () => {
                    const s = document.querySelector('.reveal .slides section.present');
                    if (!s) return { error: 'no present section' };
                    const r = s.getBoundingClientRect();
                    // Find text nodes that extend past the slide bounds (bad overflow)
                    const overflowElements = [];
                    s.querySelectorAll('*').forEach(el => {
                        const er = el.getBoundingClientRect();
                        // Check if element goes past the slide's bottom edge
                        if (er.bottom > r.bottom + 2) {
                            const txt = (el.innerText || el.textContent || '').trim().slice(0, 60);
                            if (txt) overflowElements.push({
                                tag: el.tagName,
                                cls: el.className.toString().slice(0, 40),
                                text: txt,
                                offset_below: Math.round(er.bottom - r.bottom)
                            });
                        }
                    });
                    // Find lowest text baseline for "content density" measurement
                    let maxBottom = r.top;
                    s.querySelectorAll('*').forEach(el => {
                        if (!el.innerText && !el.textContent) return;
                        const txt = (el.innerText || '').trim();
                        if (!txt) return;
                        const er = el.getBoundingClientRect();
                        // Only count elements mostly inside the slide
                        if (er.top < r.bottom && er.bottom > r.top && er.bottom <= r.bottom + 5) {
                            if (er.bottom > maxBottom) maxBottom = er.bottom;
                        }
                    });
                    const slideHeight = r.height;
                    const contentBottom = maxBottom - r.top;
                    const emptyFrac = Math.max(0, 1 - (contentBottom / slideHeight));
                    // Body background — is the canvas outside the slide white?
                    const bodyBg = getComputedStyle(document.body).backgroundColor;
                    const htmlBg = getComputedStyle(document.documentElement).backgroundColor;
                    return {
                        slide_rect: { w: Math.round(r.width), h: Math.round(r.height) },
                        content_bottom_px: Math.round(contentBottom),
                        empty_fraction: Math.round(emptyFrac * 100) / 100,
                        overflow_count: overflowElements.length,
                        overflow_items: overflowElements.slice(0, 6),
                        body_bg: bodyBg,
                        html_bg: htmlBg,
                    };
                }
            """)
            info["slide"] = i
            info["screenshot"] = str(shot)
            findings.append(info)

        await browser.close()

    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deck", type=pathlib.Path, required=True)
    ap.add_argument("--out",  type=pathlib.Path, required=True)
    ap.add_argument("--slides", type=int, default=15)
    args = ap.parse_args()

    findings = asyncio.run(audit(args.deck, args.out, args.slides))

    # Write JSON report
    (args.out / "audit.json").write_text(json.dumps(findings, indent=2) + "\n")

    # Pretty print summary
    print(f"\n{'Slide':<6}{'Bottom':<10}{'Empty%':<9}{'Overflow':<10}{'Body BG':<22}{'Notes'}")
    print("-" * 100)
    for f in findings:
        i = f["slide"]
        cb = f.get("content_bottom_px", 0)
        ef = int((f.get("empty_fraction", 0) or 0) * 100)
        ov = f.get("overflow_count", 0)
        bg = f.get("body_bg", "?")
        notes = []
        if ef > 35:
            notes.append(f"much empty space ({ef}%)")
        if ov > 0:
            notes.append(f"{ov} overflow")
        if bg and "255, 255, 255" in bg:
            notes.append("WHITE body bg!")
        print(f"{i:<6}{cb:<10}{ef:<9}{ov:<10}{bg:<22}{', '.join(notes) if notes else 'ok'}")

    print(f"\nFull report: {args.out / 'audit.json'}")
    print(f"Screenshots: {args.out}")


if __name__ == "__main__":
    sys.exit(main())
