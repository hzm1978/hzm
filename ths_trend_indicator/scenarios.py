"""Deterministic synthetic OHLC, never reconstructed user-market data."""
import numpy as np


def path(prices, steps=18, tail=9, prefix=50):
    if isinstance(steps,int): steps=[steps]*(len(prices)-1)
    close=np.r_[np.full(prefix,prices[0]),
                np.concatenate([np.linspace(a,b,s,endpoint=False)
                                for a,b,s in zip(prices,prices[1:],steps)]),prices[-1]]
    if tail:
        sign=1 if prices[-1]<prices[-2] else -1
        close=np.r_[close,prices[-1]+sign*np.arange(1,tail+1)*50/max(1,tail)]
    open_=np.r_[close[0]-1,close[:-1]]
    return {'C':close,'O':open_,'H':np.maximum(open_,close)+1,
            'L':np.minimum(open_,close)-1}


def sample(name):
    if name in ('same_three','same_four','contract_equal','recouple_equal','white_same','graded_expand','graded_contract'):
        cases={
            'same_three':([100,120,112,132,124,144,136,156],[10,6,10,6,10,6,10],98),
            'same_four':([100,120,112,132,124,144,136,156,148,168],[10,6,10,6,10,6,10,6,10],98),
            'contract_equal':([100,150,125,175,155,205,185,235],[15,18,15,8,15,8,15],98),
            'recouple_equal':([100,150,125,175,155,205,185,235,210,260],[15,18,15,8,15,8,15,18,15],98),
            'graded_expand':([100,150,125,175,150,200,175,225],[15,6,8,14,10,30,18],98),
            'graded_contract':([100,150,125,175,150,200,175,225],[15,30,18,14,10,6,8],98),
            'white_same':([6705,7040,7000,7200,7150,8035,7750,8254,7465,8769,8094,9055,8494,9845],
                          [8,2,8,2,15,12,12,8,18,10,12,12,12],6704),
        }
        prices,steps,origin=cases[name];d=path(prices,steps,tail=0)
        return d,{'DIR':1,'MANF':1,'MFROM':origin,'MTO':prices[-1]+1,
                  'MFAGE':len(d['C'])-1-(50 if name=='white_same'else 0),'KIND':1}
    if name=='white_same_down':
        data,settings=sample('white_same')
        reflected={'O':20000-data['O'],'C':20000-data['C'],
                   'H':20000-data['L'],'L':20000-data['H']}
        return reflected,dict(settings)|{'DIR':-1,'MFROM':20000-settings['MFROM'],'MTO':20000-settings['MTO']}
    # Conceptual regressions inspired by interval feedback. These paths
    # are explicitly synthetic and cannot verify the user's exact chart.
    if name in ('q_early','q_failed','q_merge','q_tiny','s_main','s_fib'):
        cases={
            'q_early':([3907,3720,3740,3680,3725,3200,3280,3130,3250],[14,5,5,4,20,3,7,3],-1,3908,3129),
            'q_failed':([8271,8656,8369,9285,8840,9194,9100],[15,18,20,14,10,3],1,8270,9195),
            'q_merge':([200,160,180,152,175,100,130],[10,22,8,23,40,5],-1,201,99),
            'q_tiny':([4987,4950,4948,4946,4944,4890,4593,4775,4471,4600],[10,1,1,1,4,18,10,18,3],-1,4988,4470),
            's_main':([451859,374501,439749,337401],[36,28,48],-1,451860,337400),
            's_fib':([3907,3720,3740,3680,3725,3200,3280,3130,3340,3220,3500,3380,3624,3550],[14,5,5,4,20,3,7,14,6,12,6,14,4],-1,3908,3129),
        }
        seq,steps,direction,first,last=cases[name]
        data=path(seq,steps,tail=0)
        if name=='q_tiny':data['L'][60]=4900
        return data,{'DIR':direction,'MANF':1,'MFROM':first,'MTO':last,
                     'MFAGE':len(data['C'])-1-50,'KIND':1 if name=='s_main'else 2}
    if name=='q_failed_auto':
        data,settings=sample('q_failed')
        return data,{'DIR':1}
    if name=='q_reversal':
        data=path([433.3,620,501,928.4,650,768.9,605.8],[22,12,28,12,24,9],tail=0)
        data['O'][0]=data['C'][0];data['L'][0]=data['C'][0]-1
        return data,{'DIR':0}
    if name=='main_left':
        return path([825.5,732.4,764.1,694.5,740.5,667.7],[30,16,22,28,24],tail=0),{'DIR':-1}
    if name=='main_three':
        return path([3512,3185,3414,3142],[35,20,35],tail=0),{'DIR':-1}
    if name=='guides_up':
        return path([100,140,120,160,135,180],[15,10,16,12,18],tail=0),{'DIR':1}
    if name=='target_bases':
        return ({k:np.full(100,v)for k,v in (('O',3225.),('C',3220.),('H',3230.),('L',3210.))},
                {'DIR':-1,'KIND':1,'MH1':200,'MBASE':3500,'MORG':4000,'MAGE':5})
    if name=='main_owned':
        # Inspired by parent boundary feedback; synthetic, not user OHLC.
        return path([1080,865.7,910,880,1014.38,960,990,923,977,920,950,901,930,870,900,832,876],
                    [18,18,8,18,10,4,5,5,8,4,6,4,7,5,8,10],tail=0),{'DIR':-1}
    if name=='p_white_up':
        data,settings=sample('p_white')
        reflected={'O':600-data['O'],'C':600-data['C'],
                   'H':600-data['L'],'L':600-data['H']}
        return reflected,dict(settings)|{'DIR':1,'MFROM':600-settings['MFROM'],'MTO':600-settings['MTO']}
    if name=='p_s_correction':
        data=path([1200,1000,1100,900,1000,824,890,860,920,880,940],8,tail=0)
        return data,{'DIR':-1,'KIND':2,'MANF':1,'MFROM':1201,'MTO':823,'MFAGE':len(data['C'])-1-50}
    if name=='p_newtip':
        return path([2000,1800,1950,1823,2021,1439,1510],
                    [18,18,18,18,32,3],tail=0),{'DIR':-1}
    if name in ('p_whole','p_white','p_triangle','p_no_h1'):
        if name=='p_whole':
            data=path([1014.38,960,990,923,977,920,950,901,930,870,900,832,876],
                      [10,4,5,5,8,4,6,4,7,5,8,10],tail=0)
        elif name=='p_white':
            data=path([200,100,130,101,150,70,100,40,85],
                      [10,4,3,4,10,5,8,9],tail=0)
            # An intrabar lower low is not the FIRST close-Q break.
            data['L'][67]=97.5
        elif name=='p_triangle':
            data=path([200,100,130,110,125,114,120,80,130],
                      [12,5,5,5,5,5,8,10],tail=0)
        else:
            data=path([15315,13000,14200],[45,10],tail=0)
        end={'p_whole':831,'p_white':39,'p_triangle':79,'p_no_h1':12999}[name]
        return data,{'DIR':-1,'KIND':2,'MANF':1,'MFROM':float(data['H'][50]),
                     'MTO':end,'MFAGE':len(data['C'])-1-50}
    if name=='earlier_complete':
        # The first completed parent impulse has five internal children;
        # it must remain annotated while a later impulse is displayed.
        return path([17000,14016,15400,15000,16000,15600,16974,16000,18149],
                    [18,8,2,6,2,8,18,24],tail=10,prefix=40),{'DIR':1}
    if name=='short_inner_q':
        # Conceptual regression inspired by a missed inner bounce, NOT
        # reconstructed user-market OHLC. Wicks make the final complete
        # leg 2947.7 -> 2639.2, while actual Q lengths are 5, 2, 5.
        prices=[3300,2900,3180,2800,3100,2700,3020,2800,
                2946.7,2799,2880,2730,2760,2660,2740,2640.2]
        return path(prices,[18]*8+[12,2,4,1,2,3,2],tail=10,prefix=40),None
    if name=='parent_correction':
        # A three-bar local bounce remains inside a parent pullback.
        # A positive H1 supplies targets only if phase is classified wrongly.
        return path([16270,18440,17000,19000,18500,21004,18905,19050],
                    [18,18,18,18,24,12,3],tail=0,prefix=40),{'DIR':1,'MH1':200,'MBASE':19000,'MORG':18500,'MAGE':5}
    if name=='sideways':
        data=path([100,140,102,139,103,138,104,137],18,tail=0)
        for close in np.linspace(136.5,132,10):
            open_=data['C'][-1]
            for key,value in [('C',close),('O',open_),('H',max(close,open_)+1),('L',min(close,open_)-1)]:
                data[key]=np.r_[data[key],value]
        return data,{'MH1':20,'MBASE':100,'MORG':80,'MAGE':5}
    if name=='five_up':
        return path([6000,5000,5400,5200,5600,5400,5800]),None
    if name=='nested_five_down':
        return path([6500,5500,6300,5700,6400,6000,6250,5750,6000,5500],4),None
    if name=='seven_expanding':
        return path([6000,5000,5400,5200,5600,5400,5800,5600,6000],
                    [18,18,18,18,30,24,40,32]),None
    if name=='triangle_e':
        data=path([100,140,120,160,140,180,160,200,160,192,170,186,176],18,tail=0)
        for close in (178,182,188):
            open_=data['C'][-1]
            for key,v in [('C',close),('O',open_),('H',max(close,open_)+1),('L',min(close,open_)-1)]:
                data[key]=np.r_[data[key],v]
        return data,None
    if name=='fall_rebound':
        return path([4500,4800,4000,4400,3700,4100,3400,3800,3100,3500,
                     2800,3200,2500,2900,2200,2550],22,tail=0,prefix=0),None
    if name=='trend_targets':
        return ({k:np.full(100,v)for k,v in
                 (('O',3305.),('C',3300.),('H',3310.),('L',3290.))},
                {'DIR':-1,'KIND':1,'MH1':200,'MBASE':3500,'MORG':4000,'MAGE':5})
    raise ValueError(name)


NAMES=('fall_rebound','five_up','nested_five_down','seven_expanding','triangle_e','trend_targets',
       'short_inner_q','parent_correction','sideways','earlier_complete',
       'p_whole','p_white','p_triangle','p_no_h1','p_newtip','p_s_correction','p_white_up','main_owned',
       'q_early','q_failed','q_merge','q_tiny','s_main','s_fib','q_failed_auto','q_reversal',
       'main_left','main_three','target_bases','guides_up',
       'same_three','same_four','contract_equal','recouple_equal','white_same','white_same_down',
       'graded_expand','graded_contract')
