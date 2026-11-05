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


NAMES=('fall_rebound','five_up','nested_five_down','seven_expanding','triangle_e','trend_targets')
