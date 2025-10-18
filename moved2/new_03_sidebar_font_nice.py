#!/usr/bin/env python3
# epub_viewer_full_adw.py — EPUB viewer with fonts, themes, images, columns (GTK4 + Libadwaita + WebKit6)

import os, sys, re, html, base64
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")
from gi.repository import Gtk, Adw, Gio, GLib, Gdk, WebKit, Pango
from ebooklib import epub

Adw.init()

# -------- CSS ----------
_SIDEBAR_CSS = b"""
.sidebar-wrap { background-color:@surface; padding:8px; }
.toc-contents-label { padding:4px 8px; font-weight:600; }
.toc-expander-row,.toc-leaf { min-height:30px; border-radius:8px; margin-right:4px; padding:4px 8px; }
.toc-active { background-color:rgba(20,80,160,0.15); font-weight:600; }
"""

# -------- HTML template ----------
_READER_TEMPLATE = """<!DOCTYPE html><html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<style>
body{margin:0;padding:2em;line-height:1.6;font-size:17px;color:#222;background:#fafafa;
font-family:-apple-system,system-ui,"Segoe UI",sans-serif;}
.chapter{margin:1em auto;max-width:900px;padding:1.5em;border-radius:8px;
box-shadow:0 1px 3px rgba(0,0,0,.08);}
img{max-width:100%;display:block;margin:1em auto;}
h1,h2,h3,h4{margin-top:1em;color:#222;}
@media(prefers-color-scheme:dark){
 body{background:#111;color:#ddd;}
 h1,h2,h3,h4{color:#eee;}
 .chapter{background:#1e1e1e;color:#ddd;}
}
</style>
<style id="epub-style">__EPUB_CSS__</style>
<style id="dyn-style"></style>
</head><body>__CONTENT__
<script>
let cfg={theme:'light',font:null,size:17,line:1.6,margin:2};
function applyCfg(){
 let c=document.getElementById('dyn-style');
 let bg='#fafafa',fg='#222',chbg='#fff';
 if(cfg.theme==='dark'){bg='#111';fg='#ddd';chbg='#1e1e1e';}
 else if(cfg.theme==='sepia'){bg='#f4ecd8';fg:'#4b3e2f';chbg:'#fdf6e3';}
 c.textContent=
  `body{background:${bg};color:${fg};font-family:${cfg.font||'-apple-system,system-ui'};font-size:${cfg.size}px;
    line-height:${cfg.line};padding:${cfg.margin}em;}
   .chapter{background:${chbg};margin:auto;max-width:900px;padding:${cfg.margin}em;border-radius:8px;}
   img{max-width:100%;}`;
}
window.setTheme=t=>{cfg.theme=t;applyCfg();};
window.setFontFamily=f=>{cfg.font=f;applyCfg();};
window.setFontSize=s=>{cfg.size=s;applyCfg();};
window.setLineHeight=l=>{cfg.line=l;applyCfg();};
window.setMargins=m=>{cfg.margin=m;applyCfg();};
window.scrollToSection=(id)=>{
  const el=document.querySelector('[data-toc-id="'+id+'"]');
  if(el)el.scrollIntoView({behavior:'smooth'});
};
applyCfg();
</script></body></html>"""

# --------- MAIN WINDOW ----------
class EPubViewerWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="EPUB Viewer")
        self.set_default_size(1200, 800)

        prov = Gtk.CssProvider(); prov.load_from_data(_SIDEBAR_CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.split = Adw.OverlaySplitView(show_sidebar=False)
        self.set_content(self.split)

        # sidebar (Adw stack)
        self.sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.sidebar_box.add_css_class("sidebar-wrap")
        self.viewstack = Adw.ViewStack()
        self.switcher = Adw.ViewSwitcher(stack=self.viewstack)
        self.sidebar_box.append(self.switcher)
        self.sidebar_box.append(self.viewstack)

        # toc
        self.toc_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        toc_scroll = Gtk.ScrolledWindow(); toc_scroll.set_child(self.toc_box)
        self.viewstack.add_titled(toc_scroll, "toc", "Contents")

        # font tab
        self.font_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_start=8, margin_end=8)
        self._build_font_tab(self.font_box)
        self.viewstack.add_titled(self.font_box, "font", "Font")

        self.split.set_sidebar(self.sidebar_box)

        # content side
        self.toolbar = Adw.ToolbarView()
        self.header = Adw.HeaderBar()
        self.header.set_title_widget(Gtk.Label(label="EPUB Viewer"))
        self.toolbar.add_top_bar(self.header)
        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toolbar.set_content(self.content_box)
        self.split.set_content(self.toolbar)

        self._build_header()
        self.book=None; self._row_map={}; self._active=None
        self.connect("notify::default-width", self._on_resize)

    # ---------- FONT TAB ----------
    def _build_font_tab(self, box):
        def label(t): box.append(Gtk.Label(label=t, xalign=0))

        label("Theme")
        self.theme_combo = Gtk.DropDown.new_from_strings(["Light","Dark","Sepia"])
        self.theme_combo.connect("notify::selected-item", lambda *_: self._apply_theme())
        box.append(self.theme_combo)

        label("Font Family")
        self.font_combo = Gtk.DropDown.new_from_strings(
            ["System","Georgia","Times New Roman","Arial","Verdana","Roboto","Open Sans","Lora"]
        )
        self.font_combo.connect("notify::selected-item", lambda *_: self._apply_font())
        box.append(self.font_combo)

        label("Font Size")
        adj = Gtk.Adjustment(value=17, lower=10, upper=40, step_increment=1)
        self.font_spin = Gtk.SpinButton(adjustment=adj)
        self.font_spin.connect("value-changed", lambda *_: self._apply_font())
        box.append(self.font_spin)

        label("Line Height")
        adj2 = Gtk.Adjustment(value=1.6, lower=1.0, upper=2.5, step_increment=0.1)
        self.line_spin = Gtk.SpinButton(adjustment=adj2, digits=1)
        self.line_spin.connect("value-changed", lambda *_: self._apply_line())
        box.append(self.line_spin)

        label("Margins")
        adj3 = Gtk.Adjustment(value=2.0, lower=0.5, upper=5.0, step_increment=0.1)
        self.margin_spin = Gtk.SpinButton(adjustment=adj3, digits=1)
        self.margin_spin.connect("value-changed", lambda *_: self._apply_margin())
        box.append(self.margin_spin)

    def _js(self, code):
        if hasattr(self, "web"): self.web.evaluate_javascript(code, -1, None, None, None, None)

    def _apply_theme(self): self._js(f"window.setTheme('{self.theme_combo.get_selected_item().get_string().lower()}')")
    def _apply_font(self):
        f=self.font_combo.get_selected_item().get_string(); s=self.font_spin.get_value()
        self._js(f"window.setFontFamily('{f}')"); self._js(f"window.setFontSize({s})")
    def _apply_line(self): self._js(f"window.setLineHeight({self.line_spin.get_value()})")
    def _apply_margin(self): self._js(f"window.setMargins({self.margin_spin.get_value()})")

    # ---------- HEADER ----------
    def _build_header(self):
        open_btn=Gtk.Button(label="Open EPUB"); open_btn.connect("clicked",self._open)
        self.header.pack_start(open_btn)

        m=Gio.Menu()
        m.append("Single","app.layout.single")
        m.append("2 Cols","app.layout.2cols")
        m.append("3 Cols","app.layout.3cols")
        m.append("Width 400px","app.layout.width")
        mb=Gtk.MenuButton(label="Layout"); mb.set_menu_model(m)
        self.header.pack_start(mb)

        t=Gtk.Button(label="Toggle Sidebar")
        t.connect("clicked",lambda *_:self.split.set_show_sidebar(not self.split.get_show_sidebar()))
        self.header.pack_end(t)

        c=Gtk.Button(label="Close")
        c.connect("clicked",lambda *_:self._reset())
        self.header.pack_end(c)

    # ---------- FILE OPEN ----------
    def _open(self,*_):
        dlg=Gtk.FileDialog(title="Open EPUB")
        f=Gtk.FileFilter(); f.add_pattern("*.epub"); dlg.set_default_filter(f)
        dlg.open(self,None,self._on_open_done)
    def _on_open_done(self,dlg,res):
        try:
            f=dlg.open_finish(res)
            if f:self._load(f.get_path())
        except Exception as e:self._err(str(e))

    # ---------- LOAD EPUB ----------
    def _load(self,path):
        try:self.book=epub.read_epub(path)
        except Exception as e:return self._err(f"Cannot open: {e}")
        toc=self._parse_toc(); self._assign_ids(toc)
        html=self._build_html(toc)
        self._show(toc,html)

    def _parse_toc(self):
        def rec(it):
            n={"href":None,"title":None,"children":[]}
            if isinstance(it,(list,tuple)):
                for e in it:
                    if hasattr(e,"href"):n["href"]=e.href
                    if hasattr(e,"title"):n["title"]=e.title
                    if isinstance(e,(list,tuple)):n["children"].append(rec(e))
            else:
                if hasattr(it,"href"):n["href"]=it.href
                if hasattr(it,"title"):n["title"]=it.title
            return n
        return [rec(x) for x in getattr(self.book,"toc",[]) or []]

    def _assign_ids(self,nodes,c=0):
        for n in nodes:
            c+=1; n["toc_id"]=f"toc-{c}"
            if n.get("children"):c=self._assign_ids(n["children"],c)
        return c

    def _collect_css(self):
        parts=[]
        for it in self.book.get_items():
            if "css" in str(it.media_type).lower():
                css=it.get_content().decode("utf-8","ignore")
                css=re.sub(r"url\\(([^)]+)\\)", lambda m:self._embed_url(m,it), css)
                parts.append(css)
        return "\n".join(parts)

    def _embed_url(self,m,css_item):
        url=m.group(1).strip("'\"")
        if url.startswith("data:") or url.startswith("http"): return m.group(0)
        base=os.path.dirname(getattr(css_item,"href",""))
        path=os.path.normpath(os.path.join(base,url)).replace("\\","/")
        for it in self.book.get_items():
            href=getattr(it,"href",None)
            if href and self._match(href,path):
                data=base64.b64encode(it.get_content()).decode("ascii")
                return f"url('data:{self._mime(href)};base64,{data}')"
        return m.group(0)

    def _embed_imgs(self,html_text,base_href):
        def rep(m):
            src=m.group(1).strip("'\"")
            if src.startswith("data:") or src.startswith("http"): return m.group(0)
            base=os.path.dirname(base_href)
            path=os.path.normpath(os.path.join(base,src)).replace("\\","/")
            for it in self.book.get_items():
                href=getattr(it,"href",None)
                if href and self._match(href,path):
                    data=base64.b64encode(it.get_content()).decode("ascii")
                    return f'src="data:{self._mime(href)};base64,{data}"'
            return m.group(0)
        return re.sub(r'src=["\']([^"\']+)["\']', rep, html_text, flags=re.I)

    def _mime(self,href):
        ext=href.split(".")[-1].lower()
        return {
            "jpg":"image/jpeg","jpeg":"image/jpeg","png":"image/png","gif":"image/gif",
            "svg":"image/svg+xml","css":"text/css","ttf":"font/ttf","otf":"font/otf",
            "woff":"font/woff","woff2":"font/woff2"
        }.get(ext,"application/octet-stream")

    def _match(self,a,b):
        a=a.lower().strip("/"); b=b.lower().strip("/")
        return a==b or a.endswith(b) or b.endswith(a) or a.split("/")[-1]==b.split("/")[-1]

    def _chapter_html(self,n):
        href=n.get("href"); title=html.escape(n.get("title") or "")
        if not href:return""
        base=href.split("#")[0]
        for it in self.book.get_items():
            ih=getattr(it,"href",None)
            if ih and self._match(ih,base):
                txt=it.get_content().decode("utf-8","ignore")
                txt=self._embed_imgs(txt,ih)
                body=re.search(r"<body[^>]*>(.*?)</body>",txt,re.S|re.I)
                inner=body.group(1) if body else txt
                return f'<div class="chapter" data-toc-id="{n["toc_id"]}"><h2>{title}</h2>{inner}</div>'
        return""

    def _build_html(self,toc):
        parts=[]
        for n in toc:
            parts.append(self._chapter_html(n))
            for c in n.get("children",[]): parts.append(self._chapter_html(c))
        css=self._collect_css()
        return _READER_TEMPLATE.replace("__EPUB_CSS__",css).replace("__CONTENT__","\n".join([p for p in parts if p]) or "<p>No content</p>")

    # ---------- DISPLAY ----------
    def _show(self,toc,html):
        self._clear(self.content_box)
        self.web=WebKit.WebView(); self.web.evaluate_javascript("console.log('webview ready')",-1,None,None,None,None)
        self.web.load_html(html,"file:///")
        self.content_box.append(self.web)
        self.split.set_show_sidebar(True)
        self._build_toc(self.toc_box,toc)

    # ---------- TOC ----------
    def _build_toc(self,box,nodes,level=0):
        self._clear(box)
        for n in nodes:self._add_toc(box,n,level)

    def _add_toc(self,parent,node,level):
        tid=node.get("toc_id"); title=GLib.markup_escape_text(node.get("title") or "")
        r=Adw.ActionRow(activatable=True)
        lbl=Gtk.Label(label=title,xalign=0); lbl.set_ellipsize(Pango.EllipsizeMode.END); lbl.set_hexpand(True)
        r.set_child(lbl)
        if level:r.set_margin_start(20*level)
        r.connect("activated",lambda *_:self._js(f"window.scrollToSection('{tid}')"))
        parent.append(r); self._row_map[tid]=r
        for c in node.get("children") or []:self._add_toc(parent,c,level+1)

    def _clear(self,b):
        w=b.get_first_child()
        while w:
            n=w.get_next_sibling(); b.remove(w); w=n

    def _reset(self):
        self.book=None; self._clear(self.content_box); self._clear(self.toc_box)
        self.split.set_show_sidebar(False)

    def _on_resize(self,*_):
        self.split.set_collapsed(self.get_width()<760)

    def _err(self,t):
        d=Adw.MessageDialog.new(self,"Error",t)
        d.add_response("ok","OK"); d.present()

# ---------- APP ----------
class EPubApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.example.EpubViewer",flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.connect("startup",self._start)
    def _start(self,*_):
        for name,js in [
            ("layout.single","window.setColumnLayout('single')"),
            ("layout.2cols","window.setColumnLayout('count',2)"),
            ("layout.3cols","window.setColumnLayout('count',3)"),
            ("layout.width","window.setColumnLayout('width',400)")
        ]:
            a=Gio.SimpleAction.new(name,None)
            a.connect("activate",lambda _,__,j=js:self._run(j))
            self.add_action(a)
    def _run(self,js):
        if hasattr(self,"win") and getattr(self.win,"web",None):
            self.win.web.evaluate_javascript(js,-1,None,None,None,None)
    def do_activate(self):
        if not self.props.active_window:self.win=EPubViewerWindow(self)
        self.win.present()

def main(argv):return EPubApp().run(argv)
if __name__=="__main__":sys.exit(main(sys.argv))

