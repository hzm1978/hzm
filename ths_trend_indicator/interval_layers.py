"""Three visible levels: main context, whole P, and only the last segment."""
from book_layers import Builder
from hierarchy_scope import build as scopes
from interval_engine import Engine
from interval_render import draw as render,adjustment
from interval_prices import draw as prices


def observations(b,h,main):
    """One observation per completion bar, assigned to its visible level."""
    for bull in (True,False):
        tag='HB'if bull else'HS';code='BPCODE'if bull else'SPCODE'
        s=main['scope'];d=s['direction']
        near=lambda a:f'(VX<={a} AND VX>={a}-3)'
        trend_side=f'{d}='+('(0-1)'if bull else'1')
        terms=[f'IF({trend_side},{near(s["end"])},{near(s["start"])})']
        for q in main['q']:
            terms.append(f'({q["show"]} AND IF({trend_side},{near(q["a"])},{near(q["t"])}))')
        # Running BARSLAST on a retrospectively filled pivot mask is not
        # equivalent in the native engine. Compare frozen candle ages.
        near_main=b.put(tag+'MAIN',f'{s["on"]} AND ('+' OR '.join(terms)+')')
        fine='GLC'if bull else'GHC'
        near_fine=f'COUNT({fine},0)>0 AND BARSLAST({fine})<=3'
        on=b.put(tag+'ON',f'SHOWK AND {h["P"]["on"]} AND VX<=MAX({h["M"]["start"]},{h["P"]["start"]}) AND FILTER(({near_main} OR ({near_fine})) AND {code}>0,3)')
        area=b.put(tag+'AREA',f'IF({near_main},1,IF(VX>{h["P"]["start"]},0,IF(VX>={h["P"]["end"]},2,3)))')
        for level,label in ((1,'M'),(2,'P'),(3,'S')):
            price='L-BPAD*3.3'if bull else'H+BPAD*3.3'
            for number in range(1,14):
                b.draw(f"KTEXT({on} AND {area}={level} AND {code}={number},0,{price},2,COLORWHITE,'{label} RV{number:02}'),FONTSIZE9;")


def layers():
    b=Builder();h=scopes(b)
    b.owner='new_M'
    main=Engine(b,'MQ',h['M']).build('B','BN',3)
    main_points=render(b,main,'M','COLORYELLOW',5.7,with_patterns=False)
    b.owner='new_P'
    # The child owns ONE impulse in this selected main family. It cannot
    # select a new origin from its own small pivots or reset the parent's tip.
    use_main=b.put('PQUSEMAIN',f'MANF=0 AND {main["proof"]} AND {main["count"]}>0 AND {main["base_age"]}>{h["P"]["end"]}')
    pscope=dict(h['P'])
    pscope['first']=b.frozen('PQOWNERFIRST',f'IF({use_main},{main["base"]},{h["P"]["first"]})')
    pscope['start']=b.frozen('PQOWNERSTART',f'IF({use_main},{main["base_age"]},{h["P"]["start"]})')
    pscope['on']=b.put('PQENABLED',f'{pscope["on"]} AND SHOWSUB AND {pscope["start"]}>{pscope["end"]}')
    h=h|{'P':pscope}
    b.owner='new_M';observations(b,h,main);b.owner='new_P'
    primary=Engine(b,'PQ',pscope).build('G','GN',5)
    render(b,primary,'P','COLORLIBLUE',0.7,with_patterns=False)
    b.owner='prices';plan=prices(b,h,primary)
    b.owner='new_S'
    correction=b.put('SQCORRECTION',f'{h["phase"]}=2 OR {h["phase"]}=3')
    start=b.frozen('SQOWNERSTART',h['P']['end'])
    first=b.frozen('SQOWNERFIRST',h['P']['last'])
    direction=b.frozen('SQOWNERDIR',f'(0-{h["P"]["direction"]})')
    mask=b.put('SQOWNERMASK',f'VX<{start}')
    z=b.extreme('SQOWNERZ',mask,f'{direction}*IF({direction}=1,H,L)',True)
    last=b.frozen('SQOWNERLAST',f'{direction}*{z}')
    end,has=b.first('SQOWNEREND',f'{mask} AND ABS(IF({direction}=1,H,L)-{last})<=BEPS')
    on=b.frozen('SQOWNERON',f'SHOWGRAND AND {correction} AND {h["P"]["on"]} AND {has} AND {start}>{end} AND {direction}*({last}-{first})>BEPS AND {direction}*(C-{first})>BEPS')
    last_scope=dict(first=first,start=start,last=last,end=end,direction=direction,on=on)
    final=Engine(b,'SQ',last_scope).build('G','GN',3)
    render(b,final,'S','COLORCYAN',0.8,with_patterns=False)
    # A correction's overall form is separate from its finer internal Qs.
    outer=Engine(b,'SO',last_scope|{'direction':h['P']['direction']}).geometry('AD',first,start,'0')
    outer_q=dict(show=f'{on} AND {correction}',r=first,a=start,p=outer['p'],t=outer['t'],shape=outer)
    adjustment(b,'SOUTER',outer_q,h['P']['direction'],lane=4)
    b.owner='new_M'
    b.draw(f'工作方向:{h["P"]["direction"]},NODRAW;')
    b.draw(f'末段状态:{h["phase"]},NODRAW;')
    b.draw('方向模式:DIR,NODRAW;');b.draw('标准K:SK,NODRAW;')
    b.draw(f'行情类型:{h["regime"]},NODRAW;')
    b.owner='prices'
    b.draw(f'价格尺状态:{plan["phase"]},NODRAW;')
    b.draw(f'价格尺方向:{plan["direction"]},NODRAW;')
    b.draw(f'工作回调百分比:{plan["ret"]},NODRAW;')
    return b.lines,{'M':main,'P':primary,'S':final,'hierarchy':h,'plan':plan,'last_adjustment':outer}
