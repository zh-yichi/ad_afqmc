import os, time
import numpy as np
import scipy as sp
os.environ['XLA_FLAGS'] = '--xla_force_host_platform_device_count=1 --xla_cpu_multi_thread_eigen=false intra_op_parallelism_threads=1'
os.environ['JAX_PLATFORM_NAME'] = 'cpu'
os.environ['JAX_ENABLE_X64'] = 'True'
#os.environ['JAX_DISABLE_JIT'] = 'True'
import jax.numpy as jnp
import jax.scipy as jsp
from jax import lax, jit, custom_jvp, vmap, random, vjp, jvp, checkpoint, dtypes

from functools import partial
print = partial(print, flush=True)

@custom_jvp
def _eigh(a):
    w, v = jnp.linalg.eigh(a)
    return w, v

@_eigh.defjvp
def _eigh_jvp(primals, tangents):
    a = primals[0]
    at = tangents[0]
    w, v = primal_out = _eigh(*primals)

    deg_thresh = 1.e-5
    eji = w[..., np.newaxis, :] - w[..., np.newaxis]
    #idx = abs(eji) < deg_thresh
    #eji = eji.at[idx].set(1.e200)
    eji = jnp.where(eji == 0., 1., eji)
    eji = jnp.where(abs(eji) < deg_thresh, 1.e200, eji)
    #eji = eji.at[jnp.diag_indices_from(eji)].set(1.)
    #eji = eji.at[idx].set(1.e200)
    #eji = eji.at[np.diag_indices_from(eji)].set(1.)
    eye_n = jnp.eye(a.shape[-1])
    Fmat = jnp.reciprocal(eji) - eye_n
    dw, dv = _eigh_jvp_jitted_nob(v, Fmat, at)
    return primal_out, (dw,dv)

@jit
def _eigh_jvp_jitted_nob(v, Fmat, at):
    vt_at_v = jnp.dot(v.conj().T, jnp.dot(at, v))
    dw = jnp.diag(vt_at_v)
    dv = jnp.dot(v, jnp.multiply(Fmat, vt_at_v))
    return dw, dv

@jit
def qr_vmap(walkers):
  walkers, _ = vmap(jnp.linalg.qr)(walkers)
  return walkers

@jit
def qr_vmap_uhf(walkers):
  walkers[0], _ = vmap(jnp.linalg.qr)(walkers[0])
  walkers[1], _ = vmap(jnp.linalg.qr)(walkers[1])
  return walkers

# modified cholesky for a given matrix
@partial(jit, static_argnums=(1,))
def modified_cholesky(mat, norb):
    diag = mat.diagonal()
    size = mat.shape[0]
    #norb = (jnp.sqrt(1+8*size).astype('int8') - 1)//2
    nchol_max = size
    nu = jnp.argmax(diag)
    delta_max = diag[nu]
    
    def scanned_fun(carry, x):
        carry['Mapprox'] += carry['chol_vecs'][x] * carry['chol_vecs'][x]
        delta = diag - carry['Mapprox']
        nu = jnp.argmax(jnp.abs(delta))
        delta_max = jnp.abs(delta[nu])
        #R = (carry['chol_vecs'][:x + 1, nu]).dot(carry['chol_vecs'][:x + 1, :])
        R = (carry['chol_vecs'][:, nu]).dot(carry['chol_vecs'])
        carry['chol_vecs'] = carry['chol_vecs'].at[x+1].set((mat[nu] - R) / (delta_max)**0.5)
        return carry, x
    
    carry = {}
    carry['Mapprox'] = jnp.zeros(size)
    carry['chol_vecs'] = jnp.zeros((nchol_max, nchol_max))
    carry['chol_vecs'] = carry['chol_vecs'].at[0].set(mat[nu] / delta_max**0.5)
    carry, x = lax.scan(scanned_fun, carry, jnp.arange(nchol_max-1))

    tril = jnp.tril_indices(norb)
    def scanned_fun_1(carry, x):    
        chol_mat = jnp.zeros((norb, norb))
        chol_mat = chol_mat.at[tril].set(x)
        chol_mat = chol_mat.T.at[tril].set(x)
        return carry, chol_mat.reshape(-1)
    
    _, chol_vecs = lax.scan(scanned_fun_1, 1., carry['chol_vecs'])
    return chol_vecs
