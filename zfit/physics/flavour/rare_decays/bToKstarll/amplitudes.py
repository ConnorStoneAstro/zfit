from __future__ import print_function, division, absolute_import

import tensorflow as tf

from zfit.core import tfext
from zfit.physics.flavour.form_factors import ff_parametrization as ff
from zfit.physics import constants as const
import zfit.physics.functions as funcs
from zfit.physics.flavour import ckm_parameters as ckm
from .non_local_hadronic import nlh_parametrization_from_analycity as nlh
from .. import wilson_coefficients as wc


# Normalization taken from C. Bobeth, M. Chrzaszcz, D. van Dyk and J. Virto (in preparation)
def normalize_amplitudes(q2, ml):
    return (const.GF * const.alpha_e * ckm.Vtb * ckm.Vts *
            tf.sqrt((q2 * funcs.beta(q2, ml) *
                     tf.sqrt(funcs.calc_lambda(tf.square(const.MB), tf.square(const.MKst), q2))) /
                    (3.0 * tf.pow(tf.cast(2, tf.float64), 10) * tf.pow(tfext.pi, 5) * const.MB)))


# Initial implementation of the transversity amplitudes using
# C. Bobeth, G. Hiller and D. van Dyk, Phys.Rev. D87 (2013) 034016
# Needs to be validated against C. Bobeth, M. Chrzaszcz, D. van Dyk and J. Virto (in preparation)

def A_perp_L(q2, ml):
    normalization = normalize_amplitudes(q2, ml)
    return tfext.to_complex(normalization) * \
           (((wc.C9 + wc.C9p) - (wc.C10 + wc.C10p)) * tfext.to_complex(ff.F_perp(q2))
            + tfext.to_complex(2.0 * (const.Mb + const.Ms) * const.MB / q2) *
            ((wc.C7 + wc.C7p) * tfext.to_complex(ff.F_perp_T(q2)) -
             tfext.to_complex(16.0 * tf.square(tfext.pi) * const.MB / const.Mb) *
             nlh.H_perp(q2)))


def A_perp_R(q2, ml):
    normalization = normalize_amplitudes(q2, ml)
    return tfext.to_complex(normalization) *(
            ((wc.C9 + wc.C9p) + (wc.C10 + wc.C10p)) * tfext.to_complex(ff.F_perp(q2))
            + tfext.to_complex(2.0 * (const.Mb + const.Ms) * const.MB / q2) *
            ((wc.C7 + wc.C7p) * tfext.to_complex(ff.F_perp_T(q2)) - tfext.to_complex(
                16.0 * tf.square(tfext.pi) * const.MB / const.Mb) * nlh.H_perp(q2)))


def A_para_L(q2, ml):
    normalization = normalize_amplitudes(q2, ml)
    return -1. * tfext.to_complex(normalization) * (
        ((wc.C9 - wc.C9p) - (wc.C10 - wc.C10p)) * tfext.to_complex(ff.F_para(q2))
        + tfext.to_complex(2.0 * (const.Mb - const.Ms) * const.MB / q2) *
        ((wc.C7 - wc.C7p) * tfext.to_complex(ff.F_para_T(q2)) - tfext.to_complex(
            16.0 * tf.square(tfext.pi) * const.MB / const.Mb) * nlh.H_para(q2)))


def A_para_R(q2, ml):
    normalization = normalize_amplitudes(q2, ml)
    return -1. * tfext.to_complex(normalization) * (
        ((wc.C9 - wc.C9p) + (wc.C10 - wc.C10p)) * tfext.to_complex(ff.F_para(q2))
        + tfext.to_complex(2.0 * (const.Mb - const.Ms) * const.MB / q2) *
        ((wc.C7 - wc.C7p) * tfext.to_complex(ff.F_para_T(q2)) - tfext.to_complex(
            16.0 * tf.square(tfext.pi) * const.MB / const.Mb) * nlh.H_para(q2)))


def A_zero_L(q2, ml):
    normalization = normalize_amplitudes(q2, ml)
    return -1. * tfext.to_complex(normalization) * (
        ((wc.C9 - wc.C9p) - (wc.C10 - wc.C10p)) * tfext.to_complex(ff.F_zero(q2))
        + tfext.to_complex(2.0 * (const.Mb - const.Ms) * const.MB / q2) *
        ((wc.C7 - wc.C7p) * tfext.to_complex(ff.F_zero_T(q2)) - tfext.to_complex(
            16.0 * tf.square(tfext.pi) * const.MB / const.Mb) * nlh.H_zero(q2)))


def A_zero_R(q2, ml):
    normalization = normalize_amplitudes(q2, ml)
    return -1. * tfext.to_complex(normalization) * (
        ((wc.C9 - wc.C9p) + (wc.C10 - wc.C10p)) * tfext.to_complex(ff.F_zero(q2))
        + tfext.to_complex(2.0 * (const.Mb - const.Ms) * const.MB / q2) *
        ((wc.C7 - wc.C7p) * tfext.to_complex(ff.F_zero_T(q2)) - tfext.to_complex(
            16.0 * tf.square(tfext.pi) * const.MB / const.Mb) * nlh.H_zero(q2)))


def A_time(q2, ml):
    normalization = normalize_amplitudes(q2, ml)
    return (-1. * tfext.to_complex(normalization * 2.0) * (wc.C10 - wc.C10p) *
            tfext.to_complex(ff.F_time(q2)))
