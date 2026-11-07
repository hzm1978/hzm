"""Bounded Q-family reduction shared by the main, P and last-leg layers.

Raw reversals are evidence, not wave numbers. Upgrade, downgrade, equal-Q
continuation and 1.618 height extension act on the family before rendering.
"""
from book_layers import relation

def same_degree(a,b):
    return f'(({a}<=10 AND {b}<=10 AND ABS({b}-{a})<=2) OR (({a}>10 OR {b}>10) AND {b}>=0.828*{a} AND {b}<=1.208*{a}))'

def crosses_degree(a,b,up):
    small=f'{a}<=10 AND {b}<=10'
    return f'(({small} AND '+(f'{b}-{a}>5'if up else f'{a}-{b}>5')+f') OR (({a}>10 OR {b}>10) AND '+(f'{b}>2.618*{a}'if up else f'{b}<0.382*{a}')+'))'

FIELDS=('a','t','b')
META=('meta',)


class Record(dict):
    """Store candle positions; prices and presence are derived lazily.

    Root, counter-end and the role-specific return age identify a selected
    Q. Slot 0 stores its close return; later slots store their wick return.
    Raw records keep both returns until their role has been selected.
    """
    def __init__(self,engine,name,values,cache=None):
        super().__init__(values);self.engine=engine;self.name=name;self.cache=dict(cache or {})

    def __getitem__(self,key):
        if dict.__contains__(self,key):return dict.__getitem__(self,key)
        if key in self.cache:return self.cache[key]
        e=self.engine
        if key in ('acode','asnap','asource','alen'):
            expressions={'acode':f'{self["meta"]}-INTPART({self["meta"]}/16)*16',
                         'alen':f'INTPART({self["meta"]}/16)-INTPART({self["meta"]}/128)*8',
                         'asource':f'INTPART({self["meta"]}/128)-INTPART({self["meta"]}/512)*4',
                         'asnap':f'INTPART({self["meta"]}/512)'}
            result=e.put(self.name+key.upper(),expressions[key])
        elif key in ('r','p'):
            age=self['a'if key=='r'else't']
            series=f'IF({e.d}=1,'+('H,L)'if key=='r'else'L,H)')
            result=e.freeze(self.name+key.upper(),f'REF({series},MIN(DATACOUNT-1,{age}))')
        elif key=='returned':
            result=e.put(self.name+'RETURNED',f'{self["b"]}>=0')
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

    def crossings(self,name,root,age,after=None):
        mask=f'VX<{after or age} AND VX>={self.e}'
        wa,wh=self.first(name+'W',f'{mask} AND {self.d}*(IF({self.d}=1,H,L)-{root})>BEPS')
        ca,ch=self.first(name+'C',f'{mask} AND {self.d}*(C-{root})>BEPS')
        return self.put(name+'WA',f'IF({wh},{wa},DATACOUNT)'),wh,self.put(name+'CA',f'IF({ch},{ca},DATACOUNT)'),ch

    def counter(self,name,root,age,boundary):
        band=self.put(name+'BAND',f'VX<{age} AND VX>{boundary}')
        z=self.extreme(name+'Z',band,f'{self.d}*IF({self.d}=1,L,H)')
        price=self.put(name+'EXT',f'{self.d}*{z}')
        at,has=self.first(name+'EXTA',f'{band} AND ABS(IF({self.d}=1,L,H)-{price})<=BEPS')
        return dict(p=price,t=at,has=has)

    def endpoint(self,name,root,age,boundary,fallback,allow_live=False):
        """Own the latest counter turn before return, independently of labels."""
        snap=self.freeze(name+'SNAP',f'MIN(DATACOUNT-1,{boundary}+IF(REF(GTYPE,{boundary})=(0-{self.d}),0,REF(GA1,{boundary})))')
        p=self.freeze(name+'P',f'REF(GP0,{snap})')
        t=self.freeze(name+'T',f'REF(GA0,{snap})+{snap}+GN')
        roots=' OR '.join(f'(REF(GK{i},{snap}) AND ABS(REF(GP{i},{snap})-{root})<=BEPS AND ABS(REF(GA{i},{snap})+{snap}+GN-{age})<=GN)'for i in (3,5))
        use=self.put(name+'USE',f'REF(GK0,{snap}) AND REF(GTYPE,{snap})=(0-{self.d}) AND {t}<{age} AND {t}>{boundary} AND {t}<={fallback["t"]} AND {self.d}*({root}-{p})>BEPS AND ({roots})')
        p=self.freeze(name+'ENDP',f'IF({use},{p},{fallback["p"]})')
        t=self.freeze(name+'ENDT',f'IF({use},{t},{fallback["t"]})')
        if allow_live:
            ba=self.freeze(name+'BA',f'REF(GA0,{boundary})+{boundary}+GN')
            bp=self.freeze(name+'BP',f'REF(GP0,{boundary})')
            live=self.counter(name+'LIVE',bp,ba,f'({boundary}-1)')
            roots=' OR '.join(f'(REF(GK{i},{boundary}) AND ABS(REF(GP{i},{boundary})-{root})<=BEPS AND ABS(REF(GA{i},{boundary})+{boundary}+GN-{age})<=GN)'for i in (2,4))
            on=self.put(name+'LIVEON',f'REF(GTYPE,{boundary})={self.d} AND {ba}<{age} AND {ba}>{live["t"]} AND {live["has"]} AND {self.d}*(C-{bp})<(0-BEPS) AND {self.d}*({root}-{live["p"]})>BEPS AND ({roots})')
            p=self.freeze(name+'LENDP',f'IF({on},{live["p"]},{p})')
            t=self.freeze(name+'LENDT',f'IF({on},{live["t"]},{t})')
        return dict(p=p,t=t,has=fallback['has'])

    def geometry(self,name,root,age,boundary,fallback=None,allow_live=False,canonical=False):
        """Complete owned adjustment, including c/e different from extreme a."""
        extreme=fallback or self.counter(name,root,age,boundary)
        price,at,has=extreme['p'],extreme['t'],extreme['has']
        options=[]
        groups=(('G','GN',1),)if self.key.startswith(('SQ','SO'))else(('S','PN',2),('G','GN',1))
        only_g=len(groups)==1
        for source,n,source_id in groups:
            snap=self.freeze(name+source+'SNAP',f'MIN(DATACOUNT-1,{boundary}+IF(REF({source}TYPE,{boundary})=(0-{self.d}),0,REF({source}A1,{boundary})))')
            frame=self.put(name+source+'FRAME',f'REF({source}TYPE,{snap})=(0-{self.d}) AND REF({source}A0,{snap})+{snap}+{n}<{age} AND REF({source}A0,{snap})+{snap}+{n}>{boundary} AND {self.d}*({root}-REF({source}P0,{snap}))>BEPS')
            if fallback:
                frame=self.put(name+source+'LATEST',f'{frame} AND REF({source}A0,{snap})+{snap}+{n}<={at}')
            if canonical:
                frame=self.put(name+source+'OWNEND',f'{frame} AND ABS(REF({source}P0,{snap})-{price})<=BEPS AND ABS(REF({source}A0,{snap})+{snap}+{n}-{at})<={n}')
            for size in (5,3):
                match=self.put(name+source+str(size)+'MATCH',f'{frame} AND REF({source}K{size},{snap}) AND ABS(REF({source}P{size},{snap})-{root})<=BEPS AND ABS(REF({source}A{size},{snap})+{snap}+{n}-{age})<={n}')
                code=f'REF({source}AC{size},{snap})'
                options.append((match,code,snap,source_id,size,n))
        options.sort(key=lambda x:(-x[4],-x[3]))
        known_count=len(options)
        live_options=[]
        if allow_live:
            # The current c need not have a confirmed fractal yet. Its
            # endpoint is searched AFTER b, so an older extreme a cannot
            # replace a contracting correction's actual latest endpoint.
            for source,n,source_id in groups:
                la=self.freeze(name+source+'LIVEBA',f'REF({source}A0,{boundary})+{boundary}+{n}')
                lp=self.freeze(name+source+'LIVEBP',f'REF({source}P0,{boundary})')
                ap=self.freeze(name+source+'LIVEAP',f'REF({source}P1,{boundary})')
                live=self.counter(name+source+'LIVE',lp,la,f'({boundary}-1)')
                match=self.put(name+source+'LIVEMATCH',f'REF({source}TYPE,{boundary})={self.d} AND REF({source}K2,{boundary}) AND ABS(REF({source}P2,{boundary})-{root})<=BEPS AND ABS(REF({source}A2,{boundary})+{boundary}+{n}-{age})<={n} AND {la}<{age} AND {la}>{live["t"]} AND {live["has"]} AND {self.d}*({lp}-{live["p"]})>BEPS AND {self.d}*(C-{lp})<(0-BEPS)')
                code=self.put(name+source+'LIVECODE',f'IF({self.d}*({lp}-{root})>=0-BEPS,4,IF({self.d}*({live["p"]}-{ap})>BEPS,3,2))')
                live_options.append((match,code,boundary,source_id,3,n,live))
            options+=live_options
        choice='0'
        for number,item in reversed(list(enumerate(options,1))):
            match,code=item[:2]
            choice=f'IF({match} AND {code}>0,{number},{choice})'
        choice=self.freeze(name+'CHOICE',choice)
        s_indices=[i for i,item in enumerate(options,1)if item[3]==2]
        five_indices=[i for i,item in enumerate(options,1)if item[4]==5]
        s_gate=' OR '.join(f'{choice}={i}'for i in s_indices)
        five_gate=' OR '.join(f'{choice}={i}'for i in five_indices)
        source=self.put(name+'SOURCE',f'IF({s_gate},2,IF({choice}>0,1,0))'if s_indices else f'IF({choice}>0,1,0)')
        length=self.put(name+'LEN',f'IF({five_gate},5,IF({choice}>0,3,0))')
        ssnap=next((item[2]for item in options if item[3]==2),'0')
        gsnap=next(item[2]for item in options if item[3]==1)
        def select_source(s,g):return g if only_g else f'IF({source}=2,{s},{g})'
        is_live=self.put(name+'LIVE',f'{choice}>={known_count+1}')if allow_live else'0'
        selected_snap=self.put(name+'SNAP',f'IF({is_live},{boundary},'+select_source(ssnap,gsnap)+')')
        cp=f'IF({choice}>0,'+select_source(f'REF(SP0,{ssnap})',f'REF(GP0,{gsnap})')+f',{price})'
        ca=f'IF({choice}>0,'+select_source(f'REF(SA0,{ssnap})+{ssnap}+PN',f'REF(GA0,{gsnap})+{gsnap}+GN')+f',{at})'
        if allow_live:
            live_by_id={item[3]:item for item in live_options}
            lp=select_source(live_by_id.get(2,live_by_id[1])[6]['p'],live_by_id[1][6]['p'])
            la=select_source(live_by_id.get(2,live_by_id[1])[6]['t'],live_by_id[1][6]['t'])
            cp=f'IF({is_live},{lp},{cp})';ca=f'IF({is_live},{la},{ca})'
        counter=self.freeze(name+'ENDP',price if canonical else cp)
        counter_age=self.freeze(name+'ENDA',at if canonical else ca)
        # A multi-turn correction is never silently called a simple A.
        inside=self.freeze(name+'INNER',f'COUNT(IF({self.d}=1,GHC,GLC) AND VX+GN<{age} AND VX+GN>{counter_age} AND ABS(REF(IF({self.d}=1,H,L),GN)-{root})>BEPS,BC)')
        rc=f'IF({choice}>0,'+select_source(f'IF({length}=5,REF(SAC5,{ssnap}),REF(SAC3,{ssnap}))',f'IF({length}=5,REF(GAC5,{gsnap}),REF(GAC3,{gsnap}))')+',0)'
        if allow_live:
            lc=select_source(live_by_id.get(2,live_by_id[1])[1],live_by_id[1][1])
            rc=f'IF({is_live},{lc},{rc})'
        raw_code=self.freeze(name+'FRAMECODE',rc)
        code=self.put(name+'CODE',f'IF({raw_code}>0,{raw_code},IF({inside}=0,1,10))')
        return dict(p=counter,t=counter_age,has=has,code=code,
                    snap=selected_snap,source=source,length=length,live=is_live,only_g=only_g)

    def raw(self,index,source,n,cursor,previous_root,first):
        tag=f'R{index}'
        event=self.put(tag+'EVENT',f'IF({self.d}=1,{source}HC,{source}LC) AND VX+{n}<{cursor} AND VX+{n}>{self.e}')
        raw_age,found=self.first(tag+'A0',event)
        seed_age=self.put(tag+'SEEDA',f'{raw_age}+{n}')
        root=self.freeze(tag+'P',f'REF(IF({self.d}=1,H,L),MIN(DATACOUNT-1,{seed_age}))')
        earliest,earliest_has=self.first(tag+'FIRST',f'VX<{cursor} AND VX>={seed_age} AND ABS(IF({self.d}=1,H,L)-{root})<=BEPS')
        age=self.put(tag+'A',f'IF({earliest_has},{earliest},{seed_age})')
        w,wh,c,ch=self.crossings(tag,root,age)
        boundary=self.put(tag+'BOUNDARY',f'IF({first},IF({ch},{c},{self.e}),IF({wh},{w},{self.e}))')
        shape=self.counter(tag+'AD',root,age,boundary)
        close_counter=self.freeze(tag+'CLOSECOUNTER',f'COUNT(VX<{age} AND VX>{boundary} AND {self.d}*(C-REF(C,1))<(0-BEPS),BC)>0')
        valid=self.put(tag+'ON',f'{self.s["on"]} AND {found} AND {shape["has"]} AND {close_counter} AND {age}-{boundary}>=2 AND {self.d}*({root}-{shape["p"]})>BEPS')
        qa=self.put(tag+'SAFEA',f'IF({valid},{age},DATACOUNT)')
        qt=self.put(tag+'SAFET',f'IF({valid},{shape["t"]},DATACOUNT)')
        closed=self.put(tag+'CLOSED',f'IF({first},{ch},{wh})')
        returned=self.put(tag+'RETURN',f'IF({first},IF({ch},{c},0-1),IF({wh},{w},0-1))')
        record=Record(self,tag+'REC',dict(a=qa,t=qt,b=returned,boundary=boundary,closed=closed),cache={'r':root,'p':shape['p'],'v':valid,'w':w,'c':c,'wh':wh,'ch':ch})
        return record,self.put(tag+'NEXT',f'IF({found} AND IF({first},{ch},{wh}),{boundary},{self.e})')

    def merged(self,name,left,right,first,classify=False):
        # Book figs 8-5-45/46 retain the LEFT interval's root. An inner b
        # may cross it before c; the merged clock resumes AFTER that c/e.
        # Taking the minimum/maximum of the whole span incorrectly moves
        # wave 1 to the later b and erases the first visible interval.
        root=left['r'];age=left['a']
        w,wh,c,ch=self.crossings(name,root,age,after=right['t'])
        boundary=self.put(name+'BOUNDARY',f'IF({first},IF({ch},{c},{self.e}),IF({wh},{w},{self.e}))')
        fallback=dict(p=right['p'],t=right['t'],has=right['v'])
        shape=self.geometry(name+'AD',root,age,boundary,fallback=fallback)if classify else fallback
        on=self.put(name+'ON',f'{left["v"]} AND {right["v"]} AND {shape["has"]} AND {age}>{shape["t"]} AND {shape["t"]}>{boundary} AND {age}-{boundary}>=2 AND {self.d}*({root}-{shape["p"]})>BEPS')
        record=Record(self,name+'REC',{'a':self.put(name+'SAFEA',f'IF({on},{age},DATACOUNT)'),
                                      't':self.put(name+'SAFET',f'IF({on},{shape["t"]},DATACOUNT)'),
                                      'b':self.put(name+'RETURN',f'IF({first},IF({ch},{c},0-1),IF({wh},{w},0-1))')},cache={'r':root,'p':shape['p'],'v':on,'w':w,'c':c,'wh':wh,'ch':ch})
        if classify:record['meta']=self.shape_word(name+'META',shape)
        return record

    def shape_word(self,name,shape):
        return self.put(name,f'(({shape["snap"]}*4+{shape["source"]})*8+{shape["length"]})*16+{shape["code"]}')

    def n(self,q,first=False):
        return f'{q["a"]}-MAX({q["b"]},{self.e})'

    def reduce(self,raws):
        zero=Record(self,'EMPTY',dict(a='DATACOUNT',t='DATACOUNT',b='(0-1)'));state=[zero,zero,zero]
        count=self.put('N0','0');ambiguous=self.put('AMB0','0');steps=[]
        for index,incoming in enumerate(raws):
            tag=f'T{index}'
            if index==0:
                state[0]=self.choose(tag+'SEED',incoming['v'],incoming,zero)
                count=self.put(tag+'COUNT',f'IF({incoming["v"]},1,0)')
                continue
            last23=self.choose(tag+'LAST23',f'{count}=3',state[2],state[1])if index>=3 else state[1]
            last=self.choose(tag+'LAST',f'{count}>=2',last23,state[0])if index>=2 else state[0]
            before=self.choose(tag+'PREV',f'{count}=3',state[1],state[0])if index>=3 else state[0]
            old_count=count;old_first=state[0];old_last=last
            reference=self.put(tag+'REFERENCE',self.n(state[0]))
            incoming_n=self.put(tag+'NEWN',self.n(incoming))
            reference_rel=self.put(tag+'REFSAME',same_degree(reference,incoming_n))
            last_n=self.put(tag+'LASTN',self.n(last))
            before_n=self.put(tag+'BEFOREN',self.n(before))
            tail_rel=self.put(tag+'TAILSAME',same_degree(before_n,last_n))
            ready=f'{incoming["v"]} AND {incoming["returned"]}'
            hp=self.put(tag+'HPREV',f'{self.d}*({last["r"]}-{before["p"]})')
            hn=self.put(tag+'HNEW',f'{self.d}*({incoming["r"]}-{last["p"]})')
            pre_height=self.put(tag+'PREHEIGHT',f'{ready} AND {count}>=2 AND {hp}>BEPS AND {hn}>1.618*{hp}')if index>=2 else'0'
            pre_equal=self.put(tag+'PRECOUPLE',f'{ready} AND {count}=3 AND {tail_rel}')if index>=3 else'0'
            pre=self.put(tag+'PRE',f'{pre_height} OR {pre_equal}')
            left=self.choose(tag+'ML',pre,before,last)if index>=2 else last
            right=self.choose(tag+'MR',pre,last,incoming)if index>=2 else incoming
            merged=self.merged(tag+'M',left,right,f'IF({pre},{count}=2,{count}=1)')
            merged_n=self.put(tag+'MERGEDN',self.n(merged))
            merged_rel=self.put(tag+'MERGEDSAME',same_degree(reference,merged_n))
            # A complete inner pair does not silently establish a larger
            # working degree. Prefer a new raw Q that matches the original
            # reference; promote a union only when it recouples that grade.
            pre_keep=self.put(tag+'PREKEEP',f'{merged["v"]} AND ({pre_height} OR ({pre_equal} AND {reference_rel}=0 AND {merged_rel}))')
            pre_drop=self.put(tag+'PREDROP',f'{pre_equal} AND IF({pre_keep},0,1)')
            pre_done=self.put(tag+'PREDONE',f'{pre_keep} OR {pre_drop}')
            if index>=2:
                state[0]=self.choose(tag+'P0',f'{pre_keep} AND {old_count}=2',merged,state[0])
                kept_middle=self.choose(tag+'PK1',f'{pre_keep} AND {old_count}=3',merged,state[1])if index>=3 else state[1]
                state[1]=self.choose(tag+'P1',f'{pre_drop} OR ({pre_keep} AND {old_count}=2)',zero,kept_middle)
                if index>=3:state[2]=self.choose(tag+'P2',pre_done,zero,state[2])
            count=self.put(tag+'PCOUNT',f'IF({pre_drop},1,IF({pre_keep},{old_count}-1,{old_count}))')
            # Query only the metrics needed after a prefix reduction.
            # Selecting three full date records again here duplicates the
            # same conditional graph in every standalone indicator.
            last_n=self.put(tag+'PLASTN',f'IF({pre_keep},{merged_n},IF({pre_drop},{reference},{last_n}))')
            before_n=self.put(tag+'PBEFOREN',f'IF({pre_done},{reference},{before_n})')
            last_p=self.put(tag+'PLASTP',f'IF({pre_keep},{merged["p"]},IF({pre_drop},{old_first["p"]},{old_last["p"]}))')
            last_t=self.put(tag+'PLASTT',f'IF({pre_keep},{merged["t"]},IF({pre_drop},{old_first["t"]},{old_last["t"]}))')
            rel=self.put(tag+'SAME',same_degree(last_n,incoming_n))
            prior_rel=self.put(tag+'PREVSAME',same_degree(before_n,last_n))
            # Rule 1 is structural demotion, even if the two rising/falling
            # corrections cannot form a price-valid outer Q at all.
            equal_drop=self.put(tag+'EQUALDROP',f'{ready} AND {count}=2 AND {prior_rel} AND {rel}')
            intrusion=self.put(tag+'INTRUSION',f'{incoming["v"]} AND {count}>=1 AND ({self.d}*({incoming["p"]}-{last_p})<(0-BEPS) OR {last_t}<={incoming["a"]})')
            inner_pair=self.put(tag+'INNERPAIR',f'{ready} AND {count}=3 AND {rel}')
            recouple=self.put(tag+'RECOUPLE',f'{ready} AND {count}=2 AND {prior_rel}=0 AND {rel} AND {merged_rel}')
            merge_ok=self.put(tag+'MOK',f'({intrusion} OR {inner_pair} OR {recouple}) AND IF({pre} OR {equal_drop},0,1) AND {merged["v"]}')
            candidate=self.choose(tag+'C',merge_ok,merged,incoming)
            # Rule 7 is triggered by a genuinely new observed interval.
            # Synthetic unions and completed inner pairs cannot drift the
            # first surviving reference into a different degree.
            upgrade=self.put(tag+'UP',f'{ready} AND IF({merge_ok} OR {equal_drop},0,1) AND {count}>0 AND '+crosses_degree(last_n,incoming_n,True))
            downgrade=self.put(tag+'DOWN',f'{ready} AND IF({merge_ok} OR {equal_drop},0,1) AND {count}>0 AND '+crosses_degree(last_n,incoming_n,False))
            append=self.put(tag+'ADD',f'{candidate["v"]} AND IF({downgrade} OR {equal_drop},0,1)')
            first_boundary=self.put(tag+'FIRSTB',f'IF({upgrade} OR {count}=0,{incoming["c"]},{candidate["b"]})')
            promoted=Record(self,tag+'FIRST',{'a':candidate['a'],'t':candidate['t'],'b':first_boundary})
            new_state=[]
            for slot in range(3):
                if slot>index:
                    new_state.append(zero);continue
                take=self.put(tag+f'TAKE{slot}',f'{append} AND IF({upgrade},{slot}=0,IF({merge_ok},{count}={slot+1},{count}={slot}))')
                kept=self.choose(tag+f'S{slot}',take,promoted if slot==0 else candidate,state[slot])
                clear=self.put(tag+f'CLEAR{slot}',f'({upgrade} AND {slot}>0) OR ({equal_drop} AND {slot}>0)')
                new_state.append(self.choose(tag+f'F{slot}',clear,zero,kept))
            overflow=self.put(tag+'OVER',f'{append} AND IF({merge_ok} OR {upgrade},0,1) AND {count}>=3')
            ambiguous=self.put(tag+'AMB',f'{ambiguous} OR {overflow}')
            count=self.put(tag+'COUNT',f'IF({equal_drop} OR {upgrade},1,IF({append} AND IF({merge_ok},0,1),MIN(3,{count}+1),{count}))')
            state=new_state
            steps.append(dict(reference=reference,comparison_span=last_n,same_reference=reference_rel,pre_height=pre_height,
                              pre_couple=pre_equal,inner_demotion=equal_drop,pre_demotion=pre_drop,
                              upgrade=upgrade,downgrade=downgrade,count=count))
        self.steps=steps
        zero=Record(self,'EMPTYFINAL',dict(a='DATACOUNT',t='DATACOUNT',b='(0-1)'))
        # Rebuild a pair and repeat, at most twice for a three-Q family.
        for index in range(2):
            tag=f'F{index}'
            hfirst=self.put(tag+'H1',f'{self.d}*({state[0]["r"]}-{self.s["first"]})')
            hthird=self.put(tag+'H3',f'{self.d}*({state[1]["r"]}-{state[0]["p"]})')
            hfifth=self.put(tag+'H5',f'{self.d}*(IF({count}=3,{state[2]["r"]},{self.s["last"]})-{state[1]["p"]})')
            early=self.put(tag+'EARLY',f'{count}>=2 AND (({state[1]["returned"]} AND {hthird}>BEPS AND {hfifth}>1.618*{hthird}) OR {self.d}*({state[1]["p"]}-{state[0]["p"]})<(0-BEPS) OR {state[0]["t"]}<={state[1]["a"]})')
            lastpair=self.put(tag+'LASTPAIR',f'{count}=3 AND IF({early},0,1)')
            left=self.choose(tag+'L',lastpair,state[1],state[0])
            right=self.choose(tag+'R',lastpair,state[2],state[1])
            hp=self.put(tag+'HP',f'{self.d}*({right["r"]}-{left["p"]})')
            following=self.put(tag+'HT',f'{self.d}*(IF({lastpair} OR {count}=2,{self.s["last"]},{state[2]["r"]})-{right["p"]})')
            merge=self.merged(tag+'M',left,right,f'IF({lastpair},0,1)',classify=False)
            do=self.put(tag+'DO',f'{count}>=2 AND {merge["v"]} AND ({early} OR ({right["returned"]} AND {hp}>BEPS AND {following}>1.618*{hp}) OR {self.d}*({right["p"]}-{left["p"]})<(0-BEPS) OR {left["t"]}<={right["a"]})')
            shifted=self.choose(tag+'SHIFT',f'{count}=3',state[2],zero)
            middle=self.choose(tag+'MID',lastpair,merge,shifted)
            state=[self.choose(tag+'S0',f'{do} AND IF({lastpair},0,1)',merge,state[0]),
                   self.choose(tag+'S1',do,middle,state[1]),
                   self.choose(tag+'S2',do,zero,state[2])]
            count=self.put(tag+'COUNT',f'IF({do},{count}-1,{count})')
        return state,count,ambiguous

    def candidate_pool(self,raws,limit):
        """Scan widely, then grade the longest observed Qs in time order.

        Earlier two-bar noise must not consume all five family slots. The
        date tie-break is exact and keeps SORTPOS ranks distinct natively.
        All raw observations remain available for internal structure.
        """
        ranks=[];size=len(raws)
        for i,q in enumerate(raws):
            span=self.put(f'POOLN{i}',f'{q["a"]}-IF({q["wh"]},{q["w"]},{self.e})')
            q['span']=span
            ranks.append(self.put(f'POOLSCORE{i}',f'IF({q["v"]},0-({span}*{size+1}+{size-i}),DATACOUNT+1)'))
        if limit==size:
            output=[]
            for slot,q in enumerate(raws):
                q['picked']=q['v']
                c=self.put(f'POOL{slot}CLOSE',f'IF({q["c"]}<DATACOUNT,{q["c"]},0-1)')
                w=self.put(f'POOL{slot}WICK',f'IF({q["w"]}<DATACOUNT,{q["w"]},0-1)')
                b=c if slot==0 else w
                boundary=self.put(f'POOL{slot}BOUND',f'MAX({b},{self.e})')
                ending=self.endpoint(f'POOL{slot}END',q['r'],q['a'],boundary,dict(p=q['p'],t=q['t'],has=q['v']))
                output.append(Record(self,f'POOL{slot}OWN',dict(a=q['a'],t=ending['t'],b=b),cache={'c':c,'w':w,'r':q['r'],'p':ending['p']}))
            return output
        winners=[self.put(f'POOLWIN{k}',f'SORTPOS(0,{k},{",".join(ranks)})')for k in range(1,limit+1)]
        total='0';ordinals=[]
        for i,q in enumerate(raws):
            q['picked']=self.put(f'POOLPICK{i}',f'{q["v"]} AND ('+' OR '.join(f'{w}={i+1}'for w in winners)+')')
            total=self.put(f'POOLORD{i}',f'{total}+IF({q["picked"]},1,0)');ordinals.append(total)
        output=[]
        for slot in range(limit):
            gates=[f'{q["picked"]} AND {ordinal}={slot+1}'for q,ordinal in zip(raws,ordinals)]
            masks=[self.put(f'POOL{slot}MASK{i}',f'COUNT({gate},1)')for i,gate in enumerate(gates)]
            present=self.put(f'POOL{slot}PRESENT','+'.join(masks))
            def select(field):
                # Every raw AGE is explicitly finite (not a nullable price).
                # Mutually exclusive masks avoid nested IF hoisting and
                # preserve the no-selection DATACOUNT sentinel exactly.
                expression='+'.join(f'{mask}*{q[field]}'for mask,q in zip(masks,raws))+f'+DATACOUNT*(1-{present})'
                return self.put(f'POOL{slot}{field.upper()}',expression)
            a=select('a');t=select('t');c=select('c');w=select('w')
            c=self.put(f'POOL{slot}CLOSE',f'IF({c}<DATACOUNT,{c},0-1)')
            w=self.put(f'POOL{slot}WICK',f'IF({w}<DATACOUNT,{w},0-1)')
            record=Record(self,f'POOL{slot}',dict(a=a,t=t,b=c if slot==0 else w),cache={'c':c,'w':w})
            boundary=self.put(f'POOL{slot}BOUND',f'MAX({record["b"]},{self.e})')
            ending=self.endpoint(f'POOL{slot}END',record['r'],a,boundary,dict(p=record['p'],t=t,has=record['v']))
            output.append(Record(self,f'POOL{slot}OWN',dict(a=a,t=ending['t'],b=record['b']),cache={'c':c,'w':w,'r':record['r'],'p':ending['p']}))
        return output

    def build(self,source='G',n='GN',limit=5,scan_limit=None):
        scan_limit=scan_limit or max(limit,10 if source=='G'else 8)
        raws=[];cursor=self.put('CURSOR',f'MIN({self.start},BOOKBARS)');previous_root=self.s['first'];seen='0'
        for i in range(scan_limit):
            q,cursor=self.raw(i,source,n,cursor,previous_root,f'{seen}=0')
            previous_root=self.put(f'ROOT{i}',f'IF({q["v"]},{q["r"]},{previous_root})')
            seen=self.put(f'SEEN{i}',f'{seen}+IF({q["v"]},1,0)')
            raws.append(q)
        pool=self.candidate_pool(raws,limit)
        state,count,ambiguous=self.reduce(pool)
        selected=[]
        for i,q in enumerate(state):
            tag=f'Q{i}'
            boundary=self.put(tag+'BOUND',f'MAX({q["b"]},{self.e})')
            shape=self.geometry(tag+'AD',q['r'],q['a'],boundary,fallback=dict(p=q['p'],t=q['t'],has=q['v']),canonical=True)
            selected.append({k:q[k]for k in ('r','a','p','t','v')}|{'shape':shape,
                               'closed':q['returned'],'return_age':q['b'],'n':self.put(tag+'N',f'{q["a"]}-{boundary}'),'boundary':boundary,
                               'show':self.put(tag+'SHOW',f'{self.s["on"]} AND {count}>{i} AND {q["v"]}')})
        h1=self.put('H1',f'IF({count}>0,{self.d}*({selected[0]["r"]}-{self.s["first"]}),0)')
        proof=self.put('PROOF',f'{self.s["on"]} AND {count}>0 AND {selected[0]["closed"]} AND {h1}>BEPS')
        rel1=self.put('REL1',relation(selected[0]['n'],selected[1]['n']))
        rel2=self.put('REL2',relation(selected[1]['n'],selected[2]['n']))
        pending=self.put('OPEN',f'{count}>0 AND IF({count}=1,IF({selected[0]["closed"]},0,1),IF({count}=2,IF({selected[1]["closed"]},0,1),IF({selected[2]["closed"]},0,1)))')
        kind=self.put('TYPE',f'IF({pending},0,IF({count}=1,3,IF({count}=2,IF({rel1}=0,5,0),IF({count}=3 AND ABS({rel1})=1 AND ABS({rel2})<=1,IF({rel1}=1,IF({rel2}>=0,71,73),IF({rel2}<=0,72,74)),0))))')
        end_base=self.put('BASE',f'IF({count}=3,{state[2]["p"]},IF({count}=2,{state[1]["p"]},IF({count}=1,{state[0]["p"]},{self.s["first"]})))')
        end_base_age=self.put('BASEAGE',f'IF({count}=3,{state[2]["t"]},IF({count}=2,{state[1]["t"]},IF({count}=1,{state[0]["t"]},{self.start})))')
        omitted=[]
        for i,q in enumerate(raws):
            relevant=self.put(f'OMITTED{i}',f'{q["v"]} AND IF({q["picked"]},0,1) AND '+relation(selected[0]['n'],q['span'])+'>=(0-1)')
            omitted.append(relevant)
        limited=self.freeze('LIMIT',f'{self.start}>BOOKBARS OR ('+' OR '.join(omitted)+f') OR COUNT(IF({self.d}=1,{source}HC,{source}LC) AND VX+{n}<{cursor} AND VX+{n}>{self.e},BC)>0')
        return dict(key=self.key,scope=self.s,raw=raws,pool=pool,q=selected,count=count,h1=h1,
                    proof=proof,kind=kind,ambiguous=ambiguous,limited=limited,
                    base=end_base,base_age=end_base_age,rel1=rel1,rel2=rel2,pending=pending,steps=self.steps)
