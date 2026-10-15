"""
YouTube attribution block.

Required by YouTube's Developer Policies (not optional, not just good
practice):
  - Section III.F.2.a — any page showing YouTube API data must make clear
    that YouTube is the source.
  - Section III.A.1 — the product must link to YouTube's own Terms of
    Service and state that users agree to them.
  - Section 13 of the API Services ToS — no implying partnership,
    endorsement, or affiliation with YouTube without their prior approval
    (the "not affiliated with" line below is what covers this).

HOW TO ADD THIS TO app.py (you're doing this part yourself):
  1. Copy the ATTRIBUTION_HTML string below into app.py — anywhere near the
     other HTML blocks (CSS / HERO) is fine.
  2. In build_ui(), add one line near the bottom of the `with gr.Blocks()`
     section, after the existing `out = gr.HTML()` line, so it shows at
     the bottom of every page:

         gr.HTML(ATTRIBUTION_HTML)

  3. Nothing else needs to change — the CSS is already bundled into the
     string below.

NOTE on the YouTube logo: this uses plain text ("YouTube"), not the actual
YouTube logo graphic. Logo use has its own sizing/color/spacing rules under
YouTube's Branding Guidelines. If you want the real logo on the page later,
pull it from YouTube's official brand resources page rather than any other
copy of it: https://www.youtube.com/howyoutubeworks/resources/brand-resources/
"""

ATTRIBUTION_HTML = """
<style>
.vf-attribution {
  text-align: center; font-size: .78em; opacity: .55; margin: 28px 0 10px;
  padding-top: 14px; border-top: 1px solid rgba(128,128,128,.2);
}
.vf-attribution a { color: inherit; text-decoration: underline; }
</style>
<div class="vf-attribution">
  Video data provided by YouTube. This site is not affiliated with, endorsed by, or sponsored by YouTube or Google.<br>
  By using this site you also agree to <a href="https://www.youtube.com/t/terms" target="_blank" rel="noopener">YouTube's Terms of Service</a>.
</div>
"""
