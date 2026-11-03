#!/usr/bin/env python3
"""Plot the analytic formula; this is not a plot of empirical estimates."""
from pathlib import Path
import math
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def value(d,s=None):
    k=d//2 if s is None else min(s,d//2)
    return d*math.comb(2*k,k)/((d-1)*4**k)


def main():
    d=np.arange(2,65)
    fig,ax=plt.subplots(figsize=(8,4.8),layout='constrained')
    for s,label,color in [(1,r'$\mathrm{rank}(\rho)\leq1$','#1464a5'),
                          (2,r'$\mathrm{rank}(\rho)\leq2$','#268269'),
                          (4,r'$\mathrm{rank}(\rho)\leq4$','#ac6c12'),
                          (None,'Unrestricted inputs','#803f77')]:
        ax.plot(d,[value(int(x),s) for x in d],lw=2,label=label,color=color)
    ax.axhline(.5,color='#808080',ls='--',lw=1,label='Complex Haar, pure inputs')
    ax.set_xscale('log',base=2)
    ax.set_xticks([2,4,8,16,32,64],labels=['2','4','8','16','32','64'])
    ax.set_xlim(2,64);ax.set_ylim(0,1.04)
    ax.set_xlabel('Hilbert-space dimension d')
    ax.set_ylabel('Optimal guaranteed TV / imaginarity')
    ax.set_title('How an input-rank promise changes the guarantee',loc='left',pad=12)
    ax.spines[['top','right']].set_visible(False)
    ax.grid(axis='y',alpha=.15)
    ax.legend(frameon=False,fontsize=9)
    out=Path(__file__).resolve().parents[1]/'results'
    fig.savefig(out/'rank_guarantees.pdf')
    fig.savefig(out/'rank_guarantees.png',dpi=180)
    plt.close(fig)


if __name__=='__main__':
    main()
