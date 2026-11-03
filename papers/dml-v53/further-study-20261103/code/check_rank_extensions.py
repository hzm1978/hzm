#!/usr/bin/env python3
"""Numerical checks of the additional rank-promise derivations.

Analytic proofs are in note/rank_sensitive_minimax.tex. These checks compare
exact rational values, beta quadrature, direct density-matrix probabilities,
and an explicit four-dimensional quaternionic POVM. Monte Carlo does not prove
the minimax or finite-nonattainment statements. No literature claim is tested.
"""
from fractions import Fraction
from pathlib import Path
import json
import math
import platform
import numpy as np
import scipy
from scipy.integrate import quad
from scipy.special import beta, gammaln

ROOT = Path(__file__).resolve().parents[1]


def exact_value(d, k):
    return Fraction(d * math.comb(2*k, k), (d-1) * 4**k)


def frames(rng, count, d):
    u = rng.normal(size=(count, d))
    u /= np.linalg.norm(u, axis=1)[:, None]
    v = rng.normal(size=(count, d))
    v -= np.einsum('bi,bi->b', u, v)[:, None]*u
    v /= np.linalg.norm(v, axis=1)[:, None]
    return u, v


def canonical_skew(d, k):
    A = np.zeros((d, d))
    for j in range(k):
        A[2*j, 2*j+1] = -1
        A[2*j+1, 2*j] = 1
    return A


def quaternion_effects():
    L = np.array([
        [[0,-1,0,0],[1,0,0,0],[0,0,0,-1],[0,0,1,0]],
        [[0,0,-1,0],[0,0,0,1],[1,0,0,0],[0,-1,0,0]],
        [[0,0,0,-1],[0,0,-1,0],[0,1,0,0],[1,0,0,0]]], dtype=float)
    n = np.array([[1,1,1],[1,-1,-1],[-1,1,-1],[-1,-1,1]])/np.sqrt(3)
    H = 1j*L
    E = (np.eye(4)[None,:,:] + np.einsum('ia,ajk->ijk',n,H))/4
    return L, H, n, E


def main():
    rng = np.random.default_rng(20261103)
    identities = []
    maximum_quadrature_error = 0.0
    maximum_original_formula_error = 0.0
    for d in range(2, 41):
        for k in range(1, d//2+1):
            c = np.exp(gammaln((d-1)/2)-.5*np.log(np.pi)-gammaln(d/2))
            if 2*k == d:
                mu = 1.0
            else:
                b = (d-2*k)/2
                # t=x^2 removes the square-root endpoint singularity. Keeping
                # the beta normalization in the integrand controls relative
                # error even when the unnormalized beta integral is tiny.
                mu, _ = quad(lambda x: 2*x**(2*k)*(1+x)**(b-1)/beta(k,b),
                             0, 1, weight='alg', wvar=(0,b-1),
                             epsabs=1e-12, epsrel=1e-12)
            value = d*c*mu/(2*k)
            expected = float(exact_value(d,k))
            maximum_quadrature_error = max(maximum_quadrature_error,abs(value-expected))
            assert abs(value-expected) < 1e-9
        r = d//2
        n = d-1 if d%2==0 else d
        original = np.exp(gammaln(n/2)-.5*np.log(np.pi)-gammaln((n+1)/2))
        if d%2:
            original *= d/(d-1)
        error = abs(original-float(exact_value(d,r)))
        maximum_original_formula_error = max(maximum_original_formula_error,error)
        assert error < 1e-12
        identities.append({'d':d,'pure_optimum':str(exact_value(d,1)),
                           'unrestricted_optimum':str(exact_value(d,r))})

    samples = 100000
    frame_checks = []
    for d in [2,3,4,5,6,8,12,16,32]:
        u,v = frames(rng,samples,d)
        for k in sorted({1,min(2,d//2),d//2}):
            z = np.abs(np.sum(u[:,:2*k:2]*v[:,1:2*k:2]
                               -u[:,1:2*k:2]*v[:,:2*k:2],axis=1))*d/(2*k)
            expected = float(exact_value(d,k))
            estimate = float(z.mean())
            se = float(z.std(ddof=1)/np.sqrt(samples))
            if d==2:
                assert abs(estimate-expected)<1e-12
                zscore = 0.0
            else:
                assert abs(estimate-expected)<6*se
                zscore = (estimate-expected)/se
            frame_checks.append({'d':d,'k':k,'samples':samples,'expected':expected,
                                 'estimate':estimate,'standard_error':se,'z_score':zscore})

    probability_checks = []
    for d in [3,4,6,12]:
        u,v = frames(rng,samples,d)
        chi = (u+1j*v)/np.sqrt(2)
        for k in sorted({1,d//2}):
            vectors = np.zeros((k,d),dtype=complex)
            for j in range(k):
                vectors[j,2*j] = 1/np.sqrt(2)
                vectors[j,2*j+1] = 1j/np.sqrt(2)
            rho = np.einsum('ai,aj->ij',vectors,vectors.conj())/k
            assert np.linalg.eigvalsh(rho).min()>-1e-12
            assert abs(np.trace(rho)-1)<1e-12
            X = rho-rho.conj()
            assert np.linalg.matrix_rank(rho,tol=1e-10)==k
            assert np.linalg.matrix_rank(X,tol=1e-10)==2*k
            M = np.linalg.norm(X,ord='nuc')/2
            assert abs(M-1)<1e-12
            p = d*np.einsum('bi,ij,bj->b',chi.conj(),rho,chi).real
            pc = d*np.einsum('bi,ij,bj->b',chi.conj(),rho.conj(),chi).real
            assert min(p.min(),pc.min()) > -1e-12
            ratios = .5*np.abs(p-pc)/M
            expected = float(exact_value(d,k))
            se = ratios.std(ddof=1)/np.sqrt(samples)
            assert abs(ratios.mean()-expected)<6*se
            probability_checks.append({'d':d,'state_rank':k,'imaginary_direction_rank':2*k,
                                       'imaginarity':float(M),'expected':expected,
                                       'direct_probability_estimate':float(ratios.mean()),
                                       'z_score':float((ratios.mean()-expected)/se)})
        psi = rng.normal(size=d)+1j*rng.normal(size=d)
        psi /= np.linalg.norm(psi)
        rho = np.outer(psi,psi.conj())
        M = np.linalg.norm(rho-rho.conj(),ord='nuc')/2
        assert abs(M-np.sqrt(max(0,1-abs(psi@psi)**2)))<1e-12

    L,H,n,E = quaternion_effects()
    assert np.allclose(E.sum(axis=0),np.eye(4))
    assert np.linalg.eigvalsh(E).min()>-1e-12
    assert all(np.linalg.matrix_rank(e,tol=1e-10)==2 for e in E)
    for a in range(3):
        for b in range(3):
            assert np.allclose(H[a]@H[b]+H[b]@H[a],2*int(a==b)*np.eye(4))
    u,v = frames(rng,20000,4)
    psi = (u+1j*v)/np.sqrt(2)
    rho = np.einsum('bi,bj->bij',psi,psi.conj())
    p = np.einsum('kij,bji->bk',E,rho).real
    pc = np.einsum('kij,bji->bk',E,rho.conj()).real
    ratios = .5*np.abs(p-pc).sum(axis=1)
    z = np.einsum('aij,bi,bj->ba',L,u,v)
    assert np.max(abs(np.linalg.norm(z,axis=1)-1))<1e-12
    predicted = .25*np.abs(z@n.T).sum(axis=1)
    direct_error = float(np.max(abs(predicted-ratios)))
    assert direct_error<1e-12
    assert ratios.min()>=1/np.sqrt(6)-1e-12
    w = np.cross(n[0],n[1]); w/=np.linalg.norm(w)
    u0 = np.eye(4)[0]
    v0 = np.einsum('a,aij,j->i',w,L,u0)
    psi0 = (u0+1j*v0)/np.sqrt(2)
    rho0 = np.outer(psi0,psi0.conj())
    attained = .5*sum(abs(np.trace(e@(rho0-rho0.conj()))) for e in E)
    assert abs(attained-1/np.sqrt(6))<1e-12
    R1 = np.array([[0,-1,0,0],[1,0,0,0],[0,0,0,1],[0,0,-1,0]])
    blind = (np.eye(4)+1j*R1)/4
    assert np.linalg.eigvalsh(blind).min()>-1e-12
    assert abs(np.trace(blind)-1)<1e-12
    blind_M = np.linalg.norm(blind-blind.conj(),ord='nuc')/2
    blind_tv = .5*sum(abs(np.trace(e@(blind-blind.conj()))) for e in E)
    assert np.linalg.matrix_rank(blind,tol=1e-10)==2
    assert abs(blind_M-1)<1e-12 and blind_tv<1e-12

    cusp_checks = []
    theta = 1e-5
    for d in range(3,17):
        for k in range(1,d//2+1):
            A = np.zeros((d,d))
            pairs = [(0,2)] + ([(1,3)] if k>=2 else [])
            pairs += [(2*j,2*j+1) for j in range(2,k)]
            for a,b in pairs:
                A[a,b]=1; A[b,a]=-1
            assert np.linalg.matrix_rank(A,tol=1e-10)==2*k
            q=[]
            for t in [-theta,0,theta]:
                R=np.eye(d)
                R[1,1]=R[2,2]=np.cos(t)
                R[1,2]=-np.sin(t); R[2,1]=np.sin(t)
                At=R@A@R.T
                assert abs(np.linalg.norm(1j*At,ord='nuc')-2*k)<1e-10
                q.append(abs(At[0,1]))
            jump=(q[2]-q[1])/theta-(q[1]-q[0])/theta
            assert abs(jump-2)<1e-9
            cusp_checks.append({'d':d,'k':k,'sampled_derivative_jump':float(jump)})

    report={'status':'PASS_NUMERICAL_CHECKS','versions':{'python':platform.python_version(),
            'numpy':np.__version__,'scipy':scipy.__version__},
            'maximum_beta_quadrature_error':maximum_quadrature_error,
            'maximum_recovery_error_for_original_formula':maximum_original_formula_error,
            'exact_rank_promise_values':identities,'frame_checks':frame_checks,
            'direct_density_matrix_checks':probability_checks,
            'four_dimensional_four_outcome_example':{'effect_ranks':[2]*4,
            'pure_state_guarantee':float(attained),'formula': '1/sqrt(6)',
            'direct_probability_vs_quaternion_formula_error':direct_error,
            'blind_rank_two_state_imaginarity':float(blind_M),'blind_rank_two_state_tv':float(blind_tv)},
            'flat_orbit_cusp_curve_checks':cusp_checks,
            'scope':'Numerical consistency checks; no proof-assistant verification or literature/novelty audit.'}
    (ROOT/'results/rank_extension_checks.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({key:report[key] for key in ['status','maximum_beta_quadrature_error',
          'maximum_recovery_error_for_original_formula','four_dimensional_four_outcome_example']},indent=2))
    print('Frame Monte Carlo maximum |z|:',max(abs(r['z_score']) for r in frame_checks))
    print('Direct probability maximum |z|:',max(abs(r['z_score']) for r in probability_checks))


if __name__=='__main__':
    main()
