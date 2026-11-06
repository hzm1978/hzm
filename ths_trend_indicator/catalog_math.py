"""Numerical models for functions newly verified in the client export.

Missing BARSSINCEN/VALUEWHEN values stay NULL; callers must explicitly
guard them with the catalogued ISNULL/IFELSE pattern instead of multiplying by 0.
"""
import numpy as np


def extra_math(fn,args,n):
    index=np.arange(n)
    if fn=='SIGN': return np.sign(args[0])
    if fn=='INTPART': return np.trunc(args[0])
    if fn in ('HHV','LLV'):
        values,windows=args
        if np.all((windows==0)|(windows==index+1)):
            return np.maximum.accumulate(values) if fn=='HHV' else np.minimum.accumulate(values)
        out=np.full(n,np.nan)
        for i,w in enumerate(windows):
            if not np.isfinite(w):continue
            sample=values[0 if w==0 else max(0,i-int(w)+1):i+1]
            out[i]=np.max(sample)if fn=='HHV'else np.min(sample)
        return out
    if fn=='ISNULL': return np.isnan(args[0]).astype(float)
    if fn in ('SORT','SORTPOS'):
        values=np.vstack(args[2:]);mode=int(args[0][0]);rank=int(args[1][0])-1
        order=np.argsort(-values if mode else values,axis=0,kind='stable')
        chosen=order[rank]
        return chosen.astype(float)+1 if fn=='SORTPOS'else values[chosen,index]
    if fn=='MAX1': return np.max(np.vstack(args),axis=0)
    if fn=='MIN1': return np.min(np.vstack(args),axis=0)
    if fn=='VALUEWHEN':
        seen=np.maximum.accumulate(np.where(args[0]!=0,index,-1))
        return np.where(seen>=0,args[1][np.maximum(0,seen)],np.nan)
    if fn=='BARSSINCEN':
        flags,windows=args[0],args[1]
        hits=np.flatnonzero(flags)
        begin=np.maximum(0,index-windows.astype(int)+1)
        at=np.searchsorted(hits,begin)
        result=np.full(n,np.nan)
        valid=(windows>0)&(at<len(hits))
        chosen=np.zeros(n,dtype=int)
        chosen[valid]=hits[at[valid]]
        valid &= chosen<=index
        result[valid]=index[valid]-chosen[valid]
        return result
    if fn in ('HHVBARS','LLVBARS'):
        values,windows=args
        result=np.full(n,np.nan)
        for i,w in enumerate(windows):
            if not np.isfinite(w): continue
            # Actual official 5.4 arithmetic: first equal extreme in the
            # window, including the first bar. N=0 returns zero; it does
            # not inherit HHV/LLV's whole-history N=0 convention.
            if w==0:result[i]=0;continue
            begin=max(0,i-int(w)+1)
            sample=values[begin:i+1]
            at=np.argmax(sample)if fn=='HHVBARS'else np.argmin(sample)
            result[i]=i-begin-at
        return result
    return NotImplemented
