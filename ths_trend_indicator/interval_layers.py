"""Whole M/P frames, owned intervals, and the latest P leg's S frame."""
from book_layers import Builder
from hierarchy_scope import build as scopes
from interval_engine import Engine
from interval_render import draw as render,adjustment
from interval_prices import draw as prices,correction_ruler
from interval_guides import draw as guides


def observations(b,h,main):
    """One K-pattern observation per completion bar, without false degrees."""
    for bull in (True,False):
        tag='HB'if bull else'HS';code='BPCODE'if bull else'SPCODE'
        s=main['scope'];d=s['direction']
        near=lambda a:f'(VX<={a} AND VX>={a}-3)'
        trend_side=f'{d}='+('(0-1)'if bull else'1')
        terms=[f'IF({trend_side},{near(s["end"])},{near(s["start"])})']
        for q in main['q']:
            terms.append(f'({q["show"]} AND IF({trend_side},{near(q["a"])},{near(q["t"])}))')
        near_main=b.put(tag+'MAIN',f'{s["on"]} AND ('+' OR '.join(terms)+')')
        fine='GLC'if bull else'GHC'
        near_fine=f'COUNT({fine},0)>0 AND BARSLAST({fine})<=3'
        on=b.put(tag+'ON',f'SHOWK AND {h["P"]["on"]} AND VX<=MAX({h["M"]["start"]},{h["P"]["start"]}) AND FILTER(({near_main} OR ({near_fine})) AND {code}>0,3)')
        price='L-BPAD*4.5'if bull else'H+BPAD*4.5'
        for number in range(1,14):
            b.draw(f"KTEXT({on} AND {code}={number},0,{price},2,COLORWHITE,'RV{number:02}'),FONTSIZE{b.font};")


def counter_scope(b,key,parent,gate='1'):
    p=lambda k,e:b.put(key+k,e)
    f=lambda k,e:b.frozen(key+k,e)
    d=f('DIR',f'(0-{parent["direction"]})')
    start=parent['end'];first=parent['last']
    mask=p('MASK',f'VX<{start}')
    z=b.extreme(key+'Z',mask,f'{d}*IF({d}=1,H,L)',True)
    last=f('LAST',f'{d}*{z}')
    end,has=b.first(key+'END',f'{mask} AND ABS(IF({d}=1,H,L)-{last})<=BEPS')
    on=f('ON',f'({gate}) AND {parent["on"]} AND {has} AND {start}>{end} AND {start}-{end}>=PN AND {d}*({last}-{first})>BEPS AND {d}*(C-{first})>BEPS')
    return dict(first=first,start=start,last=last,end=end,direction=d,on=on)


def current_adjustment(b,key,previous,counter,lane,display='1'):
    engine=Engine(b,key,previous)
    ending=engine.endpoint('END',previous['last'],previous['end'],'0',
               dict(p=counter['last'],t=counter['end'],has=counter['on']),allow_live=True)
    shape=engine.geometry('AD',previous['last'],previous['end'],'0',fallback=ending,allow_live=True,canonical=True)
    q=dict(show=b.put(key+'DISPLAY',f'{counter["on"]} AND {display}'),r=previous['last'],a=previous['end'],p=ending['p'],t=ending['t'],shape=shape)
    pts=adjustment(b,key+'DRAW',q,previous['direction'],lane=lane)
    owner=counter|dict(last=ending['p'],end=ending['t'])
    return shape,q,pts,owner


def layers():
    b=Builder(font=11,collect_text=True);h=scopes(b)
    b.owner='new_M'
    main=Engine(b,'MQ',h['M']).build('B','BN',5,scan_limit=8)
    render(b,main,'M','COLORYELLOW',5.7,with_patterns=False)
    observations(b,h,main)
    b.owner='new_P'
    # A later M counter-point must not discard the preceding P intervals.
    pscope=h['P']
    primary=Engine(b,'PQ',pscope).build('G','GN',5,scan_limit=8)
    visible=primary|{'scope':pscope|{'on':b.put('PQDISPLAY',f'{pscope["on"]} AND SHOWSUB')}}
    render(b,visible,'P','COLORLIBLUE',1.2,with_patterns=False,internal=True)
    primary['internal']=visible['internal']
    counter=counter_scope(b,'PC',pscope,f'{h["phase"]}=2 OR {h["phase"]}=3')
    shape,q,pts,current=current_adjustment(b,'PO',pscope,counter,6,display='SHOWSUB')
    b.text(f'{q["show"]} AND {shape["length"]}=3',q['t'],f'{q["p"]}-{pscope["direction"]}*BPAD*7.3','P 3?', 'COLORMAGENTA',2)
    b.text(f'{q["show"]} AND {shape["length"]}=5',q['t'],f'{q["p"]}-{pscope["direction"]}*BPAD*7.3','P 5?', 'COLORMAGENTA',2)
    cret=b.put('PORET',f'ABS({q["p"]}-{q["r"]})/MAX(ABS({pscope["last"]}-{pscope["first"]}),0.0000001)*100')
    b.text(q['show'],q['t'],f'{q["p"]}-{pscope["direction"]}*BPAD*8.5','P RET% ', 'COLORMAGENTA',2)
    b.number(q['show'],q['t'],f'{q["p"]}-{pscope["direction"]}*BPAD*8.5',cret,'COLORMAGENTA',1)
    primary_guides=guides(b,'PGL',primary,f'{h["phase"]}=1 AND SHOWSUB')
    b.owner='prices';plan=prices(b,h,primary)
    b.owner='new_S'
    # A small P-tip reaction remains inside its current S frame. Promote a
    # materially formed P counter-leg, rather than every one-bar reaction.
    completed=b.frozen('SQCOUNTERQ',f'COUNT(IF({counter["direction"]}=1,SUQ,SDQ) AND VX<{counter["start"]} AND VX+IF({counter["direction"]}=1,SUN,SDN)<{counter["start"]},BC)>0')
    use_counter=b.frozen('SQCOUNTER',f'{counter["on"]} AND ({h["phase"]}=3 OR {completed} OR ABS({current["last"]}-{current["first"]})>=0.236*ABS({pscope["last"]}-{pscope["first"]}))')
    main_leg=dict(first=primary['base'],start=primary['base_age'],last=pscope['last'],end=pscope['end'],direction=pscope['direction'])
    owned={k:b.frozen('SQOWNER'+k.upper(),f'IF({use_counter},{current[k]},{main_leg[k]})')for k in ('first','start','last','end','direction')}
    owned['on']=b.frozen('SQOWNERON',f'SHOWGRAND AND {pscope["on"]} AND {owned["start"]}>{owned["end"]} AND {owned["direction"]}*({owned["last"]}-{owned["first"]})>BEPS')
    final=Engine(b,'SQ',owned).build('G','GN',3,scan_limit=3)
    render(b,final,'S','COLORCYAN',1.2,with_patterns=False,side=-1)
    small=counter_scope(b,'SC',owned)
    outer,sq,_,small_owner=current_adjustment(b,'SO',owned,small,4.2)
    splan=correction_ruler(b,'SF',owned,small['on'],final)
    sguides=guides(b,'SGL',final,'1')
    b.draw(f'S细级状态:IF({small["on"]},2,1),NODRAW;')
    b.draw(f'S所属P段:IF({use_counter},2,1),NODRAW;')
    b.owner='new_M'
    b.draw(f'工作方向:IF({counter["on"]},{counter["direction"]},{pscope["direction"]}),NODRAW;')
    b.draw(f'末段状态:{h["phase"]},NODRAW;')
    b.draw('方向模式:DIR,NODRAW;');b.draw('标准K:SK,NODRAW;')
    b.draw(f'行情类型:{h["regime"]},NODRAW;')
    b.owner='prices'
    b.draw(f'价格尺状态:{plan["phase"]},NODRAW;')
    b.draw(f'价格尺方向:{plan["direction"]},NODRAW;')
    b.draw(f'工作回调百分比:{plan["ret"]},NODRAW;')
    b.flush_text()
    return b.lines,{'M':main,'P':primary,'S':final,'hierarchy':h,'plan':plan,
                    'current_P':current,'current_P_shape':shape,'last_adjustment':outer,
                    'S_plan':splan,'S_parent_counter':use_counter,'S_counter':small_owner,
                    'guides':{'P':primary_guides,'S':sguides}}
