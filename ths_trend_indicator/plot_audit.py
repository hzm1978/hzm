"""Inspect exported drawing ASTs using documented QHT drawing semantics.

This is a local semantic/geometry audit, not a Windows client or its compiler.
DRAWLINE is deliberately a same-candle vertical segment, never a trend line.
"""
from pathlib import Path
import numpy as np

from check_formula import Parser, evaluate, FormulaError


def audit(source, data, overrides=None):
    env = evaluate(source, data, overrides)
    n = len(data['C'])
    index = np.arange(n)
    lines, labels = [], []

    def vec(value):
        return np.full(n, value, dtype=float) if np.isscalar(value) else np.asarray(value)

    def ev(node):
        kind = node[0]
        if kind == 'number': return vec(node[1])
        if kind == 'string': return node[1]
        if kind == 'identifier':
            return node[1] if node[1].startswith('COLOR') else env[node[1]]
        if kind == 'unary': return (-1 if node[1]=='-' else 1)*ev(node[2])
        if kind == 'binary':
            a,b=ev(node[2]),ev(node[3])
            operations={'+':np.add,'-':np.subtract,'*':np.multiply,'/':np.divide,
                        '=':np.equal,'<>':np.not_equal,'>':np.greater,'<':np.less,
                        '>=':np.greater_equal,'<=':np.less_equal,
                        'AND':np.logical_and,'OR':np.logical_or}
            return operations[node[1]](a,b)
        fn,args=node[1],[ev(a) for a in node[2]]
        if fn=='MAX': return np.maximum(*args)
        if fn=='MIN': return np.minimum(*args)
        if fn=='ABS': return np.abs(args[0])
        if fn=='REF': return args[0][np.maximum(0,index-args[1].astype(int))]
        if fn=='REFX1': return args[0][np.minimum(n-1,index+args[1].astype(int))]
        if fn=='COUNT':
            return np.array([np.count_nonzero(args[0][max(0,i-int(w)+1):i+1])
                             for i,w in enumerate(args[1])],dtype=float)
        if fn=='SUM':
            return np.array([np.sum(args[0][0 if w==0 else max(0,i-int(w)+1):i+1])
                             for i,w in enumerate(args[1])],dtype=float)
        if fn=='BACKSET':
            out=np.zeros(n)
            for i in np.flatnonzero(args[0]): out[max(0,i-int(args[1][i])+1):i+1]=1
            return out
        if fn=='FILTER':
            out=np.zeros(n); blocked=-1
            for i in np.flatnonzero(args[0]):
                if i>blocked:
                    out[i]=1; blocked=i+int(args[1][i])
            return out
        raise FormulaError(f'plot audit refuses unmodelled call {fn}')

    def add_line(st, start, end, color, fn):
        if not np.all(np.isfinite([*start,*end])):
            raise FormulaError('nonfinite drawing endpoint')
        lines.append({'start':tuple(start),'end':tuple(end),'color':color,
                      'function':fn,'statement':st.line,'styles':st.styles})

    tail=source.split('{ END_ANALYSIS',1)[1].split('}',1)[1]
    for st in Parser(tail).parse():
        if st.name:
            env[st.name]=ev(st.expression)
            continue
        fn=st.expression[1]; args=[ev(a) for a in st.expression[2]]
        if fn=='DRAWLINE':
            # User's 4.39.1.2 function library: BOTH predicates must be true
            # on the SAME candle. Different start/end candles cannot connect.
            for i in np.flatnonzero(np.logical_and(args[0],args[2])):
                add_line(st,(i,args[1][i]),(i,args[3][i]),args[4],fn)
        elif fn=='POLYLINE':
            points=np.flatnonzero(args[0])
            for a,b in zip(points,points[1:]):
                add_line(st,(a,args[1][a]),(b,args[1][b]),args[2],fn)
        elif fn in ('DRAWTEXT','DRAWNUMBER1','DRAWNUMBER'):
            color=next((s for s in st.styles if s.startswith('COLOR')),'COLORWHITE')
            if fn=='DRAWNUMBER': color=args[4]
            for i in np.flatnonzero(args[0]):
                text=args[2] if fn=='DRAWTEXT' else f'{args[2][i]:.{int(args[3][i])}f}'
                labels.append({'x':int(i),'y':float(args[1][i]),'text':text,
                               'color':color,'function':fn,'statement':st.line})
        else:
            raise FormulaError(f'plot audit refuses drawing {fn}')
    return {'lines':lines,'labels':labels,'env':env}


def save_preview(source, data, path, overrides=None, last=160):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    from matplotlib.font_manager import FontProperties
    result=audit(source,data,overrides)
    colors={'COLORCYAN':'#43d4ed','COLORYELLOW':'#e6ca4f','COLORWHITE':'#ccd6e3',
            'COLORMAGENTA':'#bf87c9','COLORRED':'#e96165','COLORGREEN':'#2cb78d'}
    def color(c): return colors.get(c,'#'+c[5:] if len(c)==11 else '#ccd6e3')
    n=len(data['C']); begin=max(0,n-last)
    fig,ax=plt.subplots(figsize=(16,6),dpi=120,facecolor='#191c22')
    ax.set_facecolor('#191c22')
    cjk=Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
    font=FontProperties(fname=str(cjk)) if cjk.exists() else None
    for i in range(begin,n):
        o,h,l,c=(data[k][i] for k in ('O','H','L','C'))
        ink='#e96165' if c>=o else '#2cb78d'
        ax.vlines(i,l,h,color=ink,lw=.7)
        ax.add_patch(Rectangle((i-.3,min(o,c)),.6,max(abs(o-c),.01),color=ink,lw=.5))
    for line in result['lines']:
        a,b=line['start'],line['end']
        if b[0]<begin: continue
        width=1.4 if 'LINETHICK2' in line['styles'] else .9
        ax.plot([a[0],b[0]],[a[1],b[1]],color=color(line['color']),lw=width)
    for label in result['labels']:
        if label['x']>=begin:
            ax.text(label['x'],label['y'],label['text'],fontsize=6.5,
                    color=color(label['color']),fontproperties=font,clip_on=True)
    visible=np.r_[data['H'][begin:],data['L'][begin:],
                  [y for line in result['lines'] for x,y in (line['start'],line['end']) if x>=begin]]
    lo,hi=float(min(visible)),float(max(visible)); margin=max((hi-lo)*.08,1)
    ax.set_ylim(lo-margin,hi+margin); ax.set_xlim(begin-1,n+2)
    ax.grid(alpha=.13); ax.tick_params(colors='#a2b2c4',labelsize=8)
    for spine in ax.spines.values(): spine.set_color('#343c48')
    ax.set_title('Synthetic OHLC regression preview - exported formula AST, not the THS client',
                 color='#a2b2c4',fontsize=10)
    fig.tight_layout(); fig.savefig(Path(path)); plt.close(fig)
    return result


if __name__=='__main__':
    import argparse
    from generate import formula_source
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('docs/previews/synthetic_fall_rebound.png'))
    parser.add_argument('--scenario',choices=['fall_rebound','trend_targets'],default='fall_rebound')
    options=parser.parse_args()
    # A deliberately synthetic long fall with internal rebounds; NOT the
    # user's instrument, timestamps or prices reconstructed from a screenshot.
    prices=[4500,4800,4000,4400,3700,4100,3400,3800,3100,3500,2800,3200,2500,2900,2200,2550]
    c=np.r_[np.concatenate([np.linspace(a,b,22,endpoint=False) for a,b in zip(prices,prices[1:])]),prices[-1]]
    o=np.r_[c[0]-1,c[:-1]]
    data={'C':c,'O':o,'H':np.maximum(o,c)+1,'L':np.minimum(o,c)-1}
    settings=None
    if options.scenario=='trend_targets':
        data={k:np.full(100,v) for k,v in (('O',3305.),('C',3300.),('H',3310.),('L',3290.))}
        settings={'DIR':-1,'KIND':1,'MH1':200,'MBASE':3500,'MORG':4000,'MAGE':5}
    options.output.parent.mkdir(parents=True,exist_ok=True)
    result=save_preview(formula_source(),data,options.output,overrides=settings,last=360)
    print(f"Synthetic preview: {options.output}; {len(result['lines'])} segments, {len(result['labels'])} labels. NOT native THS rendering.")
