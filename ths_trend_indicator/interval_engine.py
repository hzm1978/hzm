"""Bounded Q-family reduction shared by the main, P and last-leg layers.

Raw reversals are evidence, not wave numbers. Upgrade, downgrade, equal-Q
continuation and 1.618 height extension act on the family before rendering.
"""
from book_layers import relation

FIELDS=('a','t','w','c')
META=('acode','asnap','asource','alen')


class Record(dict):
    """Store candle positions; prices and presence are derived lazily.

    Copying nine values through every stack branch made the editor exceed
    its saving limit. Four ages fully identify a Q and preserve exact OHLC.
    """
    def __init__(self,engine,name,values,cache=None):
        super().__init__(values);self.engine=engine;self.name=name;self.cache=dict(cache or {})

    def __getitem__(self,key):
        if dict.__contains__(self,key):return dict.__getitem__(self,key)
        if key in self.cache:return self.cache[key]
        e=self.engine
        if key in ('r','p'):
            age=self['a'if key=='r'else't']
            series=f'IF({e.d}=1,'+('H,L)'if key=='r'else'L,H)')
            result=e.freeze(self.name+key.upper(),f'REF({series},MIN(DATACOUNT-1,{age}))')
        elif key in ('wh','ch'):
            result=e.put(self.name+key.upper(),f'{self["w"if key=="wh"else"c"]}<DATACOUNT')
        elif key=='v':
            result=e.put(self.name+'V',f'{self["a"]}<DATACOUNT AND {self["t"]}<DATACOUNT AND {self["a"]}>{self["t"]}')
        else:raise KeyError(key)
        self.cache[key]=result
        return result


class Engine:
    def __init__(self,b,key,scope):
        self.b=b;self.key=key;self.s=scope;self.serial=0
        self.d=scope['direction'];self.e=scope['end'];self.start=scope['start']

    def put(self,name,value):return self.b.put(self.key+name,value)
    def freeze(self,name,value):return self.b.frozen(self.key+name,value)
    def first(self,name,condition):return self.b.first(self.key+name,condition)
    def extreme(self,name,mask,value,maximum=False):
        return self.b.extreme(self.key+name,mask,value,maximum)

    def choose(self,name,gate,yes,no):
        fields=FIELDS+tuple(k for k in META if k in yes and k in no)
        return Record(self,name,{k:self.put(name+k.upper(),f'IF({gate},{yes[k]},{no[k]})')for k in fields})

    def crossings(self,name,root,age):
        mask=f'VX<{age} AND VX>={self.e}'
        wa,wh=self.first(name+'W',f'{mask} AND {self.d}*(IF({self.d}=1,H,L)-{root})>BEPS')
        ca,ch=self.first(name+'C',f'{mask} AND {self.d}*(C-{root})>BEPS')
        return self.put(name+'WA',f'IF({wh},{wa},DATACOUNT)'),wh,self.put(name+'CA',f'IF({ch},{ca},DATACOUNT)'),ch

    def counter(self,name,root,age,boundary):
        band=self.put(name+'BAND',f'VX<{age} AND VX>{boundary}')
        z=self.extreme(name+'Z',band,f'{self.d}*IF({self.d}=1,L,H)')
        price=self.put(name+'EXT',f'{self.d}*{z}')
        at,has=self.first(name+'EXTA',f'{band} AND ABS(IF({self.d}=1,L,H)-{price})<=BEPS')
        return dict(p=price,t=at,has=has)

    def geometry(self,name,root,age,boundary):
        """Complete owned adjustment, including c/e different from extreme a."""
        extreme=self.counter(name,root,age,boundary)
        price,at,has=extreme['p'],extreme['t'],extreme['has']
        options=[]
        for source,n,source_id in (('S','PN',2),('G','GN',1)):
            snap=self.freeze(name+source+'SNAP',f'MIN(DATACOUNT-1,{boundary}+IF(REF({source}TYPE,{boundary})=(0-{self.d}),0,REF({source}A1,{boundary})))')
            frame=self.put(name+source+'FRAME',f'REF({source}TYPE,{snap})=(0-{self.d}) AND REF({source}A0,{snap})+{snap}+{n}<{age} AND REF({source}A0,{snap})+{snap}+{n}>{boundary} AND {self.d}*({root}-REF({source}P0,{snap}))>BEPS')
            for size in (5,3):
                match=self.put(name+source+str(size)+'MATCH',f'{frame} AND REF({source}K{size},{snap}) AND ABS(REF({source}P{size},{snap})-{root})<=BEPS AND ABS(REF({source}A{size},{snap})+{snap}+{n}-{age})<={n}')
                code=f'REF({source}AC{size},{snap})'
                options.append((match,code,snap,source_id,size,n))
        options.sort(key=lambda x:(-x[4],-x[3]))
        choice='0'
        for number,(match,code,_,_,_,_)in reversed(list(enumerate(options,1))):
            choice=f'IF({match} AND {code}>0,{number},{choice})'
        choice=self.freeze(name+'CHOICE',choice)
        source=self.put(name+'SOURCE',f'IF({choice}=1 OR {choice}=3,2,IF({choice}>0,1,0))')
        length=self.put(name+'LEN',f'IF({choice}=1 OR {choice}=2,5,IF({choice}>0,3,0))')
        ssnap=next(item[2]for item in options if item[3]==2)
        gsnap=next(item[2]for item in options if item[3]==1)
        selected_snap=self.put(name+'SNAP',f'IF({source}=2,{ssnap},{gsnap})')
        counter=self.freeze(name+'ENDP',f'IF({choice}>0,IF({source}=2,REF(SP0,{ssnap}),REF(GP0,{gsnap})),{price})')
        counter_age=self.freeze(name+'ENDA',f'IF({choice}>0,IF({source}=2,REF(SA0,{ssnap})+{ssnap}+PN,REF(GA0,{gsnap})+{gsnap}+GN),{at})')
        # A multi-turn correction is never silently called a simple A.
        inside=self.freeze(name+'INNER',f'COUNT(IF({self.d}=1,GHC,GLC) AND VX+GN<{age} AND VX+GN>{counter_age} AND {self.d}*(REF(IF({self.d}=1,H,L),GN)-{root})<(0-BEPS),BC)')
        raw_code=self.freeze(name+'FRAMECODE',f'IF({choice}>0,IF({source}=2,IF({length}=5,REF(SAC5,{ssnap}),REF(SAC3,{ssnap})),IF({length}=5,REF(GAC5,{gsnap}),REF(GAC3,{gsnap}))),0)')
        code=self.put(name+'CODE',f'IF({raw_code}>0,{raw_code},IF({inside}=0,1,10))')
        return dict(p=counter,t=counter_age,has=has,code=code,
                    snap=selected_snap,source=source,length=length)

    def raw(self,index,source,n,cursor,previous_root,first):
        tag=f'R{index}'
        event=self.put(tag+'EVENT',f'IF({self.d}=1,{source}HC,{source}LC) AND VX+{n}<{cursor} AND VX+{n}>{self.e} AND {self.d}*(REF(IF({self.d}=1,H,L),{n})-{previous_root})>BEPS')
        raw_age,found=self.first(tag+'A0',event)
        age=self.put(tag+'A',f'{raw_age}+{n}')
        root=self.freeze(tag+'P',f'REF(IF({self.d}=1,H,L),MIN(DATACOUNT-1,{age}))')
        w,wh,c,ch=self.crossings(tag,root,age)
        boundary=self.put(tag+'BOUNDARY',f'IF({first},IF({ch},{c},{self.e}),IF({wh},{w},{self.e}))')
        shape=self.counter(tag+'AD',root,age,boundary)
        valid=self.put(tag+'ON',f'{self.s["on"]} AND {found} AND {shape["has"]} AND {age}-{boundary}>=2 AND {self.d}*({root}-{shape["p"]})>BEPS')
        qa=self.put(tag+'SAFEA',f'IF({valid},{age},DATACOUNT)')
        qt=self.put(tag+'SAFET',f'IF({valid},{shape["t"]},DATACOUNT)')
        record=Record(self,tag+'REC',dict(a=qa,t=qt,w=w,c=c),cache={'r':root,'p':shape['p'],'v':valid,'wh':wh,'ch':ch})
        return record,self.put(tag+'NEXT',f'IF({found} AND IF({first},{ch},{wh}),{boundary},{self.e})')

    def merged(self,name,left,right,first,classify=False):
        # Rebuild from actual extrema, not Nleft+Nright. The new root may
        # be an earlier equal wick; start/end candles are found afresh.
        mask=self.put(name+'ROOTMASK',f'VX<={left["a"]} AND VX>={right["t"]}')
        z=self.extreme(name+'ROOTZ',mask,f'{self.d}*IF({self.d}=1,H,L)',True)
        root=self.put(name+'ROOT',f'{self.d}*{z}')
        age,found=self.first(name+'ROOTA',f'{mask} AND ABS(IF({self.d}=1,H,L)-{root})<=BEPS')
        w,wh,c,ch=self.crossings(name,root,age)
        boundary=self.put(name+'BOUNDARY',f'IF({first},IF({ch},{c},{self.e}),IF({wh},{w},{self.e}))')
        shape=(self.geometry if classify else self.counter)(name+'AD',root,age,boundary)
        on=self.put(name+'ON',f'{left["v"]} AND {right["v"]} AND {found} AND {shape["has"]} AND {age}>{shape["t"]} AND {age}-{boundary}>=2 AND {self.d}*({root}-{shape["p"]})>BEPS')
        record=Record(self,name+'REC',{'a':self.put(name+'SAFEA',f'IF({on},{age},DATACOUNT)'),
                                      't':self.put(name+'SAFET',f'IF({on},{shape["t"]},DATACOUNT)'),
                                      'w':w,'c':c},cache={'r':root,'p':shape['p'],'v':on,'wh':wh,'ch':ch})
        if classify:record.update({k:shape[v]for k,v in (('acode','code'),('asnap','snap'),('asource','source'),('alen','length'))})
        return record

    def n(self,q,first=False):
        return f'{q["a"]}-IF({q["ch"] if first else q["wh"]},{q["c"] if first else q["w"]},{self.e})'

    def reduce(self,raws):
        zero=Record(self,'EMPTY',{k:'DATACOUNT'for k in FIELDS});state=[zero,zero,zero]
        count=self.put('N0','0');ambiguous=self.put('AMB0','0')
        for index,incoming in enumerate(raws):
            tag=f'T{index}'
            if index==0:
                state[0]=self.choose(tag+'SEED',incoming['v'],incoming,zero)
                count=self.put(tag+'COUNT',f'IF({incoming["v"]},1,0)')
                continue
            if index==1:
                last=state[0];before=zero
            else:
                last23=self.choose(tag+'LAST23',f'{count}=3',state[2],state[1])if index>=3 else state[1]
                last=self.choose(tag+'LAST',f'{count}>=2',last23,state[0])
                before=self.choose(tag+'PREV',f'{count}=3',state[1],state[0])if index>=3 else state[0]
            last_n=self.put(tag+'LASTN',f'IF({count}=1,{self.n(last,True)},{self.n(last)})')
            incoming_n=self.put(tag+'NEWN',self.n(incoming))
            rel=self.put(tag+'REL',relation(last_n,incoming_n))
            prior_rel=self.put(tag+'PREL',relation(self.n(before,True),last_n))
            previous_impulse=self.put(tag+'HPREV',f'{self.d}*({last["r"]}-IF({count}=1,{self.s["first"]},{before["p"]}))')
            new_impulse=self.put(tag+'HNEW',f'{self.d}*({incoming["r"]}-{last["p"]})')
            do_merge=self.put(tag+'MERGE',f'{incoming["v"]} AND {count}>=1 AND (({count}>=2 AND {rel}=0) OR {self.d}*({incoming["p"]}-{last["p"]})<(0-BEPS) OR {last["t"]}<={incoming["a"]})')
            merged=self.merged(tag+'M',last,incoming,f'{count}=1')
            merge_ok=self.put(tag+'MOK',f'{do_merge} AND {merged["v"]}')
            candidate=self.choose(tag+'C',merge_ok,merged,incoming)
            candidate_n=self.put(tag+'CN',f'IF({merge_ok} AND {count}=1,{self.n(candidate,True)},{self.n(candidate)})')
            reference_n=self.put(tag+'REFN',f'IF({merge_ok} AND {count}>=2,{self.n(before,True)},{last_n})')
            comparison=self.put(tag+'COMPARE',f'IF({merge_ok} AND {count}=1,0,'+relation(reference_n,candidate_n)+')')
            upgrade=self.put(tag+'UP',f'{candidate["v"]} AND {count}>0 AND {comparison}=2')
            downgrade=self.put(tag+'DOWN',f'{candidate["v"]} AND {count}>0 AND {comparison}=(0-2)')
            append=self.put(tag+'ADD',f'{candidate["v"]} AND IF({downgrade},0,1)')
            new_state=[]
            for slot in range(3):
                if slot>index:
                    new_state.append(zero);continue
                # Merge replaces the newest interval, upgrade subsumes the
                # preceding structure, downgrade remains inside the last leg.
                take=self.put(tag+f'TAKE{slot}',f'{append} AND IF({upgrade},{slot}=0,IF({merge_ok},{count}={slot+1},{count}={slot}))')
                kept=self.choose(tag+f'S{slot}',take,candidate,state[slot])
                clear=self.put(tag+f'CLEAR{slot}',f'{upgrade} AND {slot}>0')
                new_state.append(self.choose(tag+f'F{slot}',clear,zero,kept))
            overflow=self.put(tag+'OVER',f'{append} AND IF({merge_ok} OR {upgrade},0,1) AND {count}>=3')
            ambiguous=self.put(tag+'AMB',f'{ambiguous} OR {overflow}')
            count=self.put(tag+'COUNT',f'IF({upgrade},1,IF({append} AND IF({merge_ok},0,1),MIN(3,{count}+1),{count}))')
            state=new_state
        # Resolve c/e before height comparisons. An extreme a is not the
        # actual end of a triangle or abcde correction.
        for i,q in enumerate(state):
            boundary=self.put(f'PRE{i}BOUND',f'IF({q["ch"] if i==0 else q["wh"]},{q["c"] if i==0 else q["w"]},{self.e})')
            shape=self.geometry(f'PRE{i}AD',q['r'],q['a'],boundary)
            state[i]=Record(self,f'PRE{i}',{**{k:q[k]for k in FIELDS},'t':shape['t'],
                        'acode':shape['code'],'asnap':shape['snap'],
                        'asource':shape['source'],'alen':shape['length']},cache={'r':q['r'],'p':shape['p']})
        zero=Record(self,'EMPTYFINAL',{**{k:'DATACOUNT'for k in FIELDS},**{k:'0'for k in META}})
        # Rebuild a pair and repeat, at most twice for a three-Q family.
        for index in range(2):
            tag=f'F{index}'
            hfirst=self.put(tag+'H1',f'{self.d}*({state[0]["r"]}-{self.s["first"]})')
            hthird=self.put(tag+'H3',f'{self.d}*({state[1]["r"]}-{state[0]["p"]})')
            hfifth=self.put(tag+'H5',f'{self.d}*(IF({count}=3,{state[2]["r"]},{self.s["last"]})-{state[1]["p"]})')
            early=self.put(tag+'EARLY',f'{count}>=2 AND (({hfirst}>BEPS AND {hthird}>1.618*{hfirst}) OR ({hthird}>BEPS AND {hfifth}>1.618*{hthird}))')
            lastpair=self.put(tag+'LASTPAIR',f'{count}=3 AND IF({early},0,1)')
            left=self.choose(tag+'L',lastpair,state[1],state[0])
            right=self.choose(tag+'R',lastpair,state[2],state[1])
            hp=self.put(tag+'HP',f'{self.d}*({right["r"]}-{left["p"]})')
            following=self.put(tag+'HT',f'{self.d}*({self.s["last"]}-{right["p"]})')
            merge=self.merged(tag+'M',left,right,f'IF({lastpair},0,1)',classify=True)
            do=self.put(tag+'DO',f'{count}>=2 AND {merge["v"]} AND ({early} OR ({hp}>BEPS AND {following}>1.618*{hp}) OR {self.d}*({right["p"]}-{left["p"]})<(0-BEPS) OR {left["t"]}<={right["a"]})')
            shifted=self.choose(tag+'SHIFT',f'{count}=3',state[2],zero)
            middle=self.choose(tag+'MID',lastpair,merge,shifted)
            state=[self.choose(tag+'S0',f'{do} AND IF({lastpair},0,1)',merge,state[0]),
                   self.choose(tag+'S1',do,middle,state[1]),
                   self.choose(tag+'S2',do,zero,state[2])]
            count=self.put(tag+'COUNT',f'IF({do},{count}-1,{count})')
        return state,count,ambiguous

    def build(self,source='G',n='GN',limit=6):
        raws=[];cursor=self.put('CURSOR',f'MIN({self.start},BOOKBARS)');previous_root=self.s['first'];seen='0'
        for i in range(limit):
            q,cursor=self.raw(i,source,n,cursor,previous_root,f'{seen}=0')
            previous_root=self.put(f'ROOT{i}',f'IF({q["v"]},{q["r"]},{previous_root})')
            seen=self.put(f'SEEN{i}',f'{seen}+IF({q["v"]},1,0)')
            raws.append(q)
        state,count,ambiguous=self.reduce(raws)
        selected=[]
        for i,q in enumerate(state):
            tag=f'Q{i}'
            boundary=self.put(tag+'BOUND',f'IF({q["ch"] if i==0 else q["wh"]},{q["c"] if i==0 else q["w"]},{self.e})')
            shape={'p':q['p'],'t':q['t'],'code':q['acode'],'snap':q['asnap'],'source':q['asource'],'length':q['alen']}
            selected.append({k:q[k]for k in ('r','a','p','t','w','c','wh','ch','v')}|{'shape':shape,
                               'n':self.put(tag+'N',f'{q["a"]}-{boundary}'),'boundary':boundary,
                               'show':self.put(tag+'SHOW',f'{self.s["on"]} AND {count}>{i} AND {q["v"]}')})
        h1=self.put('H1',f'IF({count}>0,{self.d}*({selected[0]["r"]}-{self.s["first"]}),0)')
        proof=self.put('PROOF',f'{self.s["on"]} AND {count}>0 AND {selected[0]["ch"]} AND {h1}>BEPS')
        rel1=self.put('REL1',relation(selected[0]['n'],selected[1]['n']))
        rel2=self.put('REL2',relation(selected[1]['n'],selected[2]['n']))
        kind=self.put('TYPE',f'IF({count}=2,IF({rel1}=0,5,0),IF({count}=3 AND ABS({rel1})=1 AND ABS({rel2})=1,IF({rel1}=1,IF({rel2}=1,71,73),IF({rel2}=(0-1),72,74)),0))')
        end_base=self.put('BASE',f'IF({count}=3,{selected[2]["p"]},IF({count}=2,{selected[1]["p"]},IF({count}=1,{selected[0]["p"]},{self.s["first"]})))')
        end_base_age=self.put('BASEAGE',f'IF({count}=3,{selected[2]["t"]},IF({count}=2,{selected[1]["t"]},IF({count}=1,{selected[0]["t"]},{self.start})))')
        limited=self.freeze('LIMIT',f'{self.start}>BOOKBARS OR COUNT(IF({self.d}=1,{source}HC,{source}LC) AND VX+{n}<{cursor} AND VX+{n}>{self.e},BC)>0')
        return dict(key=self.key,scope=self.s,raw=raws,q=selected,count=count,h1=h1,
                    proof=proof,kind=kind,ambiguous=ambiguous,limited=limited,
                    base=end_base,base_age=end_base_age,rel1=rel1,rel2=rel2)
