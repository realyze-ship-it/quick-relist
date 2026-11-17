"""Synthetic native editor tabs with fixed-header and overflow-container layouts."""
from html import escape

LABELS = ['Listing particulars', 'Deal Particulars', 'Media', 'Portals']


def tab_html(*, placement='clean', overlay='none', child='text', size='normal'):
    placements = {'clean':'top:280px', 'header':'top:800px', 'below':'top:1800px',
                  'fixed':'position:fixed;top:30px', 'horizontal':'top:280px'}
    sizes = {'normal':'', 'tiny':'width:.25px;height:.25px;min-width:0;padding:0;border:0;overflow:hidden',
             'zero':'width:0;height:0;min-width:0;padding:0;border:0;overflow:hidden'}
    children = {'text':'Deal Particulars', 'inline':'<span>Deal Particulars</span>',
                'ripple':'Deal Particulars<span class="MuiTouchRipple-root" aria-hidden="true"><span class="MuiTouchRipple-ripple"><span class="MuiTouchRipple-child"></span></span></span>',
                'interactive':'Deal Particulars<span tabindex="0"></span>',
                'role':'Deal Particulars<span role="button"></span>',
                'nested':'Deal Particulars<span><input></span>'}
    buttons = ''.join(f'<button id="tab-{i}" type="button" role="tab" aria-selected="{str(i==0).lower()}" tabindex="{0 if i==0 else -1}" style="{sizes[size] if i==1 else ""}">{children[child] if i==1 else escape(label)}</button>' for i,label in enumerate(LABELS))
    horizontal = '#tab-0{margin-right:1000px}' if placement == 'horizontal' else ''
    cover = {'none':'display:none', 'header':'top:0;left:0;right:0;height:150px', 'full':'inset:0'}
    return f'''<style>
html,body{{margin:0;padding:0;scroll-behavior:smooth}} main{{min-height:3000px}}
#tabs{{position:absolute;left:30px;width:650px;{placements[placement]}}}
#scroller{{overflow-x:auto;overflow-y:hidden;width:100%}} [role=tablist]{{display:flex;width:max-content}}
button{{position:relative;flex-shrink:0;border:0;padding:12px;min-width:140px;height:48px;font:14px sans-serif}}
button>span{{display:block}} .MuiTouchRipple-root{{position:absolute;inset:0;pointer-events:none}}
#cover{{position:fixed;z-index:99;{cover[overlay]}}} {horizontal}
</style><h2>Edit listing - RXCU-12345 (Synthetic)</h2><main id="mainContent">
<h4>Edit listing - RXCU-12345 (Synthetic)</h4><section><div><header id="tabs"><div><div><div id="scroller"><div role="tablist">{buttons}</div></div></div></div></header></div></section>
<input value="unchanged" type="text"></main><div id="cover">Synthetic fixed header/banner footprint</div>'''
