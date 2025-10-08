import urllib.parse
from .constants import THEME_INJECTION_CSS

def wrap_html(raw_html, base_uri, css_content="", column_mode_use_width=False, column_count=1, column_width_px=200, column_gap=32):
    page_css = (css_content or "") + "\n" + THEME_INJECTION_CSS
    try:
        if column_mode_use_width:
            col_decl = f"column-width: {column_width_px}px; -webkit-column-width: {column_width_px}px;"
        else:
            col_decl = f"column-count: {column_count}; -webkit-column-count: {column_count};"
        gap_decl = f"column-gap: {column_gap}px; -webkit-column-gap: {column_gap}px;"
        fill_decl = "column-fill: auto; -webkit-column-fill: auto;"
        col_rules = (
            ".ebook-content * { -webkit-column-count: unset !important; column-count: unset !important; "
            "-webkit-column-width: unset !important; column-width: unset !important; "
            "-webkit-column-gap: unset !important; column-gap: unset !important; "
            "-webkit-column-fill: unset !important; column-fill: unset !important; }\n"
            "html, body { height: 100%; min-height: 100%; margin: 0; padding: 0; overflow-x: hidden; }\n"
            ".ebook-content {\n"
            f"  {col_decl} {gap_decl} {fill_decl}\n"
            "  height: 100vh !important;\n"
            "  min-height: 0 !important;\n"
            "  overflow-y: hidden !important;\n"
            "  box-sizing: border-box !important;\n"
            "  padding: 12px;\n"
            "}\n"
            ".single-column .ebook-content {\n"
            "  height: auto !important;\n"
            "  overflow-y: auto !important;\n"
            "  -webkit-column-width: unset !important;\n"
            "  column-width: unset !important;\n"
            "  -webkit-column-count: unset !important;\n"
            "  column-count: unset !important;\n"
            "}\n"
            ".ebook-content img, .ebook-content svg { max-width: 100%; height: auto; }\n"
        )
        page_css = col_rules + page_css
    except Exception:
        pass

    js_template = """
    <script>
    (function() {
      const GAP = __GAP__;
      function getComputedNumberStyle(el, propNames) {
        const cs = window.getComputedStyle(el);
        for (let p of propNames) {
          const v = cs.getPropertyValue(p);
          if (v && v.trim()) return v.trim();
        }
        return '';
      }
      function effectiveColumns(el) {
        try {
          let cc = parseInt(getComputedNumberStyle(el, ['column-count','-webkit-column-count']) || 0, 10);
          if (!isNaN(cc) && cc > 0 && cc !== Infinity) return cc;
          let cwRaw = getComputedNumberStyle(el, ['column-width','-webkit-column-width']);
          let cw = parseFloat(cwRaw);
          if (!isNaN(cw) && cw > 0) {
            let available = Math.max(1, el.clientWidth);
            let approx = Math.floor(available / (cw + (GAP||0)));
            return Math.max(1, approx);
          }
          return 1;
        } catch(e) { return 1; }
      }
      function columnStep(el) {
        const cs = window.getComputedStyle(el);
        const cwRaw = cs.getPropertyValue('column-width') || cs.getPropertyValue('-webkit-column-width') || '';
        const cw = parseFloat(cwRaw) || (el.clientWidth);
        const gapRaw = cs.getPropertyValue('column-gap') || cs.getPropertyValue('-webkit-column-gap') || (GAP + 'px');
        const gap = parseFloat(gapRaw) || GAP;
        const cols = effectiveColumns(el);
        let step = cw;
        if (!cwRaw || cwRaw === '' || cw === el.clientWidth) {
          step = Math.max(1, Math.floor((el.clientWidth - Math.max(0, (cols-1)*gap)) / cols));
        }
        return step + gap;
      }
      function snapToNearestColumn() {
        const cont = document.querySelector('.ebook-content');
        if (!cont) return;
        const step = columnStep(cont);
        const cur = window.scrollX || window.pageXOffset || document.documentElement.scrollLeft || 0;
        const target = Math.round(cur / step) * step;
        window.scrollTo({ left: target, top: 0, behavior: 'smooth' });
      }
      function goByColumn(delta) {
        const cont = document.querySelector('.ebook-content');
        if (!cont) return;
        const step = columnStep(cont);
        const cur = window.scrollX || window.pageXOffset || document.documentElement.scrollLeft || 0;
        const target = Math.max(0, cur + (delta>0 ? step : -step));
        window.scrollTo({ left: target, top: 0, behavior: 'smooth' });
      }
      function onWheel(e) {
        const cont = document.querySelector('.ebook-content');
        if (!cont) return;
        const cols = effectiveColumns(cont);
        if (cols <= 1) return;
        if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
          e.preventDefault();
          const dir = e.deltaY > 0 ? 1 : -1;
          goByColumn(dir);
        } else {
          if (Math.abs(e.deltaX) > 0) {
            e.preventDefault();
            const dir = e.deltaX > 0 ? 1 : -1;
            goByColumn(dir);
          }
        }
      }
      function onKey(e) {
        const cont = document.querySelector('.ebook-content');
        if (!cont) return;
        const cols = effectiveColumns(cont);
        if (cols <= 1) return;
        if (e.code === 'PageDown') {
          e.preventDefault(); goByColumn(1);
        } else if (e.code === 'PageUp') {
          e.preventDefault(); goByColumn(-1);
        } else if (e.code === 'Home') {
          e.preventDefault(); window.scrollTo({ left: 0, top: 0, behavior: 'smooth' });
        } else if (e.code === 'End') {
          e.preventDefault();
          const step = columnStep(cont);
          const max = document.documentElement.scrollWidth - window.innerWidth;
          window.scrollTo({ left: max, top: 0, behavior: 'smooth' });
        }
      }
      let rTO = null;
      function onResize() {
        if (rTO) clearTimeout(rTO);
        rTO = setTimeout(function() {
          updateMode();
          snapToNearestColumn();
          rTO = null;
        }, 120);
      }
      function updateMode() {
        const c = document.querySelector('.ebook-content');
        if (!c) return;
        const cols = effectiveColumns(c);
        if (cols <= 1) {
          document.documentElement.classList.add('single-column');
          document.body.classList.add('single-column');
          window.scrollTo({ left: 0, top: 0 });
        } else {
          document.documentElement.classList.remove('single-column');
          document.body.classList.remove('single-column');
          snapToNearestColumn();
        }
      }
      document.addEventListener('DOMContentLoaded', function() {
        try {
          updateMode();
          window.addEventListener('wheel', onWheel, { passive: false, capture: false });
          window.addEventListener('keydown', onKey, false);
          window.addEventListener('resize', onResize);
          setTimeout(updateMode, 250);
          setTimeout(snapToNearestColumn, 450);
        } catch(e) { console.error('column scripts error', e); }
      });
    })();
    </script>
    """
    js_detect_columns = js_template.replace("__GAP__", str(column_gap))
    link_intercept_script = """
    <script> (function(){ function updateDarkMode(){ if(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches){document.documentElement.classList.add('dark-mode');document.body.classList.add('dark-mode');}else{document.documentElement.classList.remove('dark-mode');document.body.classList.remove('dark-mode');}} updateDarkMode(); if(window.matchMedia){window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', updateDarkMode);} function interceptLinks(){document.addEventListener('click', function(e){var target=e.target; while(target && target.tagName!=='A'){target=target.parentElement;if(!target||target===document.body) break;} if(target && target.tagName==='A' && target.href){var href=target.href; e.preventDefault(); e.stopPropagation(); try{window.location.href=href;}catch(err){console.error('[js] navigation error:', err);} return false;} }, true);} if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded', interceptLinks);} else {interceptLinks();}})(); </script>
    """
    base_tag = f'<base href="{base_uri}"/>' if base_uri else ""
    head = (
        '<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>'
        '<meta name="color-scheme" content="light dark"/>' + base_tag +
        '<style>' + page_css + '</style>' +
        link_intercept_script + js_detect_columns
    )
    wrapped = "<!DOCTYPE html><html><head>{}</head><body><div class=\"ebook-content\">{}</div></body></html>".format(head, raw_html)
    return wrapped
