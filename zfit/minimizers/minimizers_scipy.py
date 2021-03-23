#  Copyright (c) 2021 zfit
import copy
import inspect
import math
from typing import Callable, Mapping, Optional, Union

import numpy as np
import scipy.optimize
from scipy.optimize import SR1, HessianUpdateStrategy

from ..core.parameter import set_values
from ..settings import run
from ..util.container import convert_to_container
from ..util.exception import MaximumIterationReached
from ..util.warnings import warn_experimental_feature
from .baseminimizer import (NOT_SUPPORTED, BaseMinimizer, minimize_supports,
                            print_minimization_status)
from .fitresult import FitResult
from .strategy import ZfitStrategy
from .termination import CRITERION_NOT_AVAILABLE, ConvergenceCriterion


class ScipyBaseMinimizer(BaseMinimizer):
    _VALID_SCIPY_GRADIENT = None
    _VALID_SCIPY_HESSIAN = None

    def __init__(self,
                 method: str,
                 tol: Optional[float],
                 internal_tol: Mapping[str, Optional[float]],
                 gradient: Optional[Union[Callable, str, NOT_SUPPORTED]],
                 hessian: Optional[Union[Callable, str, scipy.optimize.HessianUpdateStrategy, NOT_SUPPORTED]],
                 maxiter: Optional[Union[int, str]] = None,
                 minimizer_options: Optional[Mapping[str, object]] = None,
                 verbosity: Optional[int] = None,
                 strategy: Optional[ZfitStrategy] = None,
                 criterion: Optional[ConvergenceCriterion] = None,
                 minimize_func: Optional[callable] = None,
                 name: str = "ScipyMinimizer"
                 ) -> None:
        """Base minimizer wrapping the SciPy librarys optimize module.

        To implemend a subclass, inherit from this class and:
        - override ``_minimize`` (which has the same signature as :meth:~`BaseMinimizer.minimize`.
          and decorate it with ``minimize_supports``.
        - (optional) add the allowed methods for gradients and hessian with Class._add_derivative_methods(...)


        Args:
            method: Name of the method as given to :func:~`scipy.optimize.minimize`
            tol: |@doc:minimizer.tol| Termination value for the
                   convergence/stopping criterion of the algorithm
                   in order to determine if the minimum has
                   been found. Defaults to 1e-3. |@docend:minimizer.tol|
            maxiter: |@doc:minimizer.maxiter| Approximate number of iterations.
                   This corresponds to roughly the maximum number of
                   evaluations of the `value`, 'gradient` or `hessian`. |@docend:minimizer.maxiter|
            minimizer_options:
            verbosity: |@doc:minimizer.verbosity| Verbosity of the minimizer.
                A value above 5 starts printing more
                output with a value of 10 printing every
                evaluation of the loss function and gradient. |@docend:minimizer.verbosity|
            strategy: |@doc:minimizer.strategy| A class of type `ZfitStrategy` that takes no
                   input arguments in the init. Determines the behavior of the minimizer in
                   certain situations, most notably when encountering
                   NaNs. It can also implement a callback function. |@docend:minimizer.strategy|
            criterion: |@doc:minimizer.criterion| Criterion of the minimum. This is an
                   estimated measure for the distance to the
                   minimum and can include the relative
                   or absolute changes of the parameters,
                   function value, gradients and more.
                   If the value of the criterion is smaller
                   than ``loss.errordef * tol``, the algorithm
                   stopps and it is assumed that the minimum
                   has been found. |@docend:minimizer.criterion|
            minimize_func:
            name: |@doc:minimizer.name| Human readable name of the minimizer. |@docend:minimizer.name|
        """
        self._minimize_func = scipy.optimize.minimize if minimize_func is None else minimize_func

        minimizer_options = {} if minimizer_options is None else minimizer_options
        minimizer_options = copy.copy(minimizer_options)
        minimizer_options['method'] = method

        if 'options' not in minimizer_options:
            minimizer_options['options'] = {}

        if gradient in (True, '2-point', '3-point') and not isinstance(hessian, HessianUpdateStrategy):
            raise ValueError("Whenever the gradient is estimated via finite-differences, "
                             "the Hessian has to be estimated using one of the quasi-Newton strategies.")

        if gradient is not NOT_SUPPORTED:
            if self._VALID_SCIPY_GRADIENT is not None and gradient not in self._VALID_SCIPY_GRADIENT:
                raise ValueError(f"Requested gradient {gradient} is not a valid choice. Possible"
                                 f" gradient methods are {self._VALID_SCIPY_GRADIENT}")
            if gradient is False or gradient is None:
                gradient = 'zfit'

            elif gradient is True:
                gradient = None
            minimizer_options['grad'] = gradient

        if hessian is not NOT_SUPPORTED:
            if self._VALID_SCIPY_HESSIAN is not None and hessian not in self._VALID_SCIPY_HESSIAN:
                raise ValueError(f"Requested hessian {hessian} is not a valid choice. Possible"
                                 f" hessian methods are {self._VALID_SCIPY_HESSIAN}")
            if isinstance(hessian, scipy.optimize.HessianUpdateStrategy) and not inspect.isclass(hessian):
                raise ValueError("If `hesse` is a HessianUpdateStrategy, it has to be a class that takes `init_scale`,"
                                 " not an instance. For further modification of other initial parameters, make a"
                                 " subclass of the update strategy.")
            if hessian is True:
                hessian = None
            elif hessian is False or hessian is None:
                hessian = 'zfit'
            minimizer_options['hess'] = hessian

        self._internal_tol = internal_tol
        self._internal_maxiter = 20
        super().__init__(name=name, tol=tol, verbosity=verbosity, minimizer_options=minimizer_options,
                         strategy=strategy, criterion=criterion, maxiter=maxiter)

    @classmethod
    def _add_derivative_methods(cls, gradient=None, hessian=None):
        gradient = convert_to_container(gradient, container=set)
        hessian = convert_to_container(hessian, container=set)
        if gradient is not None:
            if cls._VALID_SCIPY_GRADIENT is None:
                cls._VALID_SCIPY_GRADIENT = set()
            cls._VALID_SCIPY_GRADIENT.update(gradient)
        if hessian is not None:
            if cls._VALID_SCIPY_HESSIAN is None:
                cls._VALID_SCIPY_HESSIAN = set()
            cls._VALID_SCIPY_HESSIAN.update(hessian)

    @classmethod
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls._VALID_SCIPY_GRADIENT is not None:
            cls._VALID_SCIPY_GRADIENT = ScipyBaseMinimizer._VALID_SCIPY_GRADIENT.copy()
        if cls._VALID_SCIPY_HESSIAN is not None:
            cls._VALID_SCIPY_HESSIAN = ScipyBaseMinimizer._VALID_SCIPY_HESSIAN.copy()

    @minimize_supports(init=True)
    def _minimize(self, loss, params, init: FitResult):
        if init:
            set_values(params=params, values=init)

        evaluator = self.create_evaluator(loss=loss, params=params)

        limits = [(run(p.lower), run(p.upper)) for p in params]
        init_values = np.array(run(params))

        minimizer_options = self.minimizer_options.copy()

        minimizer_options['bounds'] = limits

        use_gradient = 'grad' in minimizer_options
        if use_gradient:
            gradient = minimizer_options.pop('grad')
            gradient = evaluator.gradient if gradient == 'zfit' else gradient
            minimizer_options['jac'] = gradient

        use_hessian = 'hess' in minimizer_options
        if use_hessian:
            hessian = minimizer_options.pop('hess')
            hessian = evaluator.hessian if hessian == 'zfit' else hessian
            minimizer_options['hess'] = hessian

            is_update_strat = inspect.isclass(hessian) and issubclass(hessian,
                                                                      scipy.optimize.HessianUpdateStrategy)

            init_scale = 'auto'
            # get possible initial step size from previous minimizer
            if init:
                approx_step_sizes = init.hesse(params=params, method='approx')
            else:
                approx_step_sizes = None

        maxiter = self.get_maxiter(len(params))
        if maxiter is not None:
            # stop 3 iterations earlier than we
            minimizer_options['options']['maxiter'] = maxiter - 3 if maxiter > 10 else maxiter

        minimizer_options['options']['disp'] = self.verbosity > 6

        # tolerances and criterion
        criterion = self.create_criterion(loss, params)

        init_tol = min([math.sqrt(loss.errordef * self.tol), loss.errordef * self.tol * 1e3])
        internal_tol = self._internal_tol
        internal_tol = {tol: init_tol if init is None else init for tol, init in internal_tol.items()}

        valid = None
        message = None
        optimize_results = None
        for i in range(self._internal_maxiter):

            # update from previous run/result
            if use_hessian and is_update_strat:
                if not isinstance(init_scale, str):
                    init_scale = np.mean(approx_step_sizes)
                minimizer_options['hess'] = hessian(init_scale=init_scale)

            for tol, val in internal_tol.items():
                minimizer_options['options'][tol] = val

            # perform minimization
            try:
                optim_result = self._minimize_func(fun=evaluator.value, x0=init_values, **minimizer_options)
            except MaximumIterationReached as error:
                if optim_result is None:  # it didn't even run once
                    raise MaximumIterationReached("Maximum iteration reached on first wrapped minimizer call. This"
                                                  "is likely to a too low number of maximum iterations (currently"
                                                  f" {evaluator.maxiter}) or wrong internal tolerances, in which"
                                                  f" case: please fill an issue on github.") from error
                maxiter_reached = True
                valid = False
                message = "Maxiter reached, terminated without convergence"
            else:
                maxiter_reached = evaluator.niter > evaluator.maxiter

            values = optim_result['x']

            fmin = optim_result.fun
            set_values(params, values)

            optimize_results = combine_optimize_results(
                [optim_result] if optimize_results is None else [optimize_results, optim_result])
            result_prelim = FitResult.from_scipy(loss=loss,
                                                 params=params,
                                                 result=optimize_results,
                                                 minimizer=self,
                                                 edm=CRITERION_NOT_AVAILABLE,
                                                 criterion=None,
                                                 message='INTERNAL for Criterion',
                                                 valid=valid)

            if use_hessian:
                approx_step_sizes = result_prelim.hesse(params=params, method='approx')
            converged = criterion.converged(result_prelim)
            valid = converged
            edm = criterion.last_value

            if self.verbosity > 5:
                print_minimization_status(converged=converged,
                                          criterion=criterion,
                                          evaluator=evaluator,
                                          i=i,
                                          fmin=fmin,
                                          internal_tol=internal_tol)

            if converged or maxiter_reached:
                break
            init_values = values

            # update the tolerances
            self._update_tol_inplace(criterion_value=edm, internal_tol=internal_tol)

        else:
            message = f"Invalid, criterion {criterion.name} is {edm}, target {self.tol} not reached."
            valid = False
        return FitResult.from_scipy(
            loss=loss,
            params=params,
            result=optimize_results,
            minimizer=self,
            valid=valid,
            criterion=criterion,
            edm=edm,
            message=message,
            niter=evaluator.niter,
            evaluator=evaluator,
        )


class ScipyLBFGSBV1(ScipyBaseMinimizer):

    def __init__(self,
                 tol: Optional[float] = None,
                 maxcor: Optional[int] = None,
                 maxls: Optional[int] = None,
                 verbosity: Optional[int] = None,
                 gradient: Optional[Union[Callable, str]] = None,
                 maxiter: Optional[Union[int, str]] = None,
                 criterion: Optional[ConvergenceCriterion] = None,
                 strategy: Optional[ZfitStrategy] = None,
                 name: str = "SciPy L-BFGS-B V1"
                 ) -> None:
        """Local, gradient based quasi-Newton algorithm using the limited-memory BFGS approximation.

        Limited-memory BFGS is an optimization algorithm in the family of quasi-Newton methods
        that approximates the Broyden–Fletcher–Goldfarb–Shanno algorithm (BFGS) using a limited amount of
        memory (or gradients, controlled by *maxcor*).

        L-BFGS borrows ideas from the trust region methods while keeping the L-BFGS update
        of the Hessian and line search algorithms.

        |@doc:minimizer.scipy.info| This implenemtation wraps the minimizers in
        `SciPy optimize <https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.minimize.html>`_. |@docend:minimizer.scipy.info|

        Args:
            tol: |@doc:minimizer.tol| Termination value for the
                   convergence/stopping criterion of the algorithm
                   in order to determine if the minimum has
                   been found. Defaults to 1e-3. |@docend:minimizer.tol|
            maxcor: |@doc:minimizer.maxcor| Maximum number of memory history to keep
                   when using a quasi-Newton update formula such as BFGS.
                   It is the number of gradients
                   to “remember” from previous optimization
                   steps: increasing it increases
                   the memory requirements but may speed up the convergence. |@docend:minimizer.maxcor|
            maxls: |@doc:minimizer.init.maxls| Maximum number of linesearch points. |@docend:minimizer.init.maxls|
            verbosity: |@doc:minimizer.verbosity| Verbosity of the minimizer.
                A value above 5 starts printing more
                output with a value of 10 printing every
                evaluation of the loss function and gradient. |@docend:minimizer.verbosity|
            gradient: |@doc:minimizer.scipy.gradient| Define the method to use for the gradient computation
                   that the minimizer should use. This can be the
                   gradient provided by the loss itself or
                   method from the minimizer.
                   In general, using the zfit provided automatic gradient is
                   more precise and needs less computation time for the
                   evaluation compared to a numerical method.
                   The following are possible choices:
                   - If set to ``False`` or ``'zfit'`` (or ``None``; default), the
                     loss gradient (usually the automatic gradient) will be used;
                     the minimizer won't use an internal algorithm.
                   - ``True`` tells the minimizer to use its default internal
                     gradient estimation.
                   - arguments ``'2-point'`` and ``'3-point'`` specify which
                     numerical algorithm the minimizer should use in order to
                     estimate the gradient. |@docend:minimizer.scipy.gradient|
            maxiter: |@doc:minimizer.maxiter| Approximate number of iterations.
                   This corresponds to roughly the maximum number of
                   evaluations of the `value`, 'gradient` or `hessian`. |@docend:minimizer.maxiter|
            criterion: |@doc:minimizer.criterion| Criterion of the minimum. This is an
                   estimated measure for the distance to the
                   minimum and can include the relative
                   or absolute changes of the parameters,
                   function value, gradients and more.
                   If the value of the criterion is smaller
                   than ``loss.errordef * tol``, the algorithm
                   stopps and it is assumed that the minimum
                   has been found. |@docend:minimizer.criterion|
            strategy: |@doc:minimizer.strategy| A class of type `ZfitStrategy` that takes no
                   input arguments in the init. Determines the behavior of the minimizer in
                   certain situations, most notably when encountering
                   NaNs. It can also implement a callback function. |@docend:minimizer.strategy|
            name: |@doc:minimizer.name| Human readable name of the minimizer. |@docend:minimizer.name|
        """
        options = {}
        if maxcor is not None:
            options['maxcor'] = maxcor
        if maxls is not None:
            options['maxls'] = maxls

        minimizer_options = {}
        if options:
            minimizer_options['options'] = options

        scipy_tols = {'ftol': None, 'gtol': None}

        super().__init__(method="L-BFGS-B", internal_tol=scipy_tols, gradient=gradient,
                         hessian=NOT_SUPPORTED,
                         minimizer_options=minimizer_options, tol=tol, verbosity=verbosity,
                         maxiter=maxiter,
                         strategy=strategy, criterion=criterion, name=name)


ScipyLBFGSBV1._add_derivative_methods(gradient=['2-point', '3-point',
                                                # 'cs'  # works badly
                                                None, True, False, 'zfit'])


class ScipyTrustKrylovV1(ScipyBaseMinimizer):

    def __init__(self,
                 tol: Optional[float] = None,
                 inexact: Optional[bool] = None,
                 gradient: Optional[Union[Callable, str]] = None,
                 hessian: Optional[Union[Callable, str, scipy.optimize.HessianUpdateStrategy]] = SR1,
                 verbosity: Optional[int] = None,
                 maxiter: Optional[Union[int, str]] = None,
                 criterion: Optional[ConvergenceCriterion] = None,
                 strategy: Optional[ZfitStrategy] = None,
                 name: str = "SciPy trust-krylov V1"
                 ) -> None:
        """Local, gradient based (nearly) exact trust-region algorithm using matrix vector products with the hessian.

        |@doc:minimizer.scipy.info| This implenemtation wraps the minimizers in
        `SciPy optimize <https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.minimize.html>`_. |@docend:minimizer.scipy.info|

        Args:
            tol: |@doc:minimizer.tol| Termination value for the
                   convergence/stopping criterion of the algorithm
                   in order to determine if the minimum has
                   been found. Defaults to 1e-3. |@docend:minimizer.tol|
            inexact: Accuracy to solve subproblems.
                If True requires less nonlinear iterations, but more vector products.
            gradient: |@doc:minimizer.scipy.gradient| Define the method to use for the gradient computation
                   that the minimizer should use. This can be the
                   gradient provided by the loss itself or
                   method from the minimizer.
                   In general, using the zfit provided automatic gradient is
                   more precise and needs less computation time for the
                   evaluation compared to a numerical method.
                   The following are possible choices:
                   - If set to ``False`` or ``'zfit'`` (or ``None``; default), the
                     loss gradient (usually the automatic gradient) will be used;
                     the minimizer won't use an internal algorithm.
                   - ``True`` tells the minimizer to use its default internal
                     gradient estimation.
                   - arguments ``'2-point'`` and ``'3-point'`` specify which
                     numerical algorithm the minimizer should use in order to
                     estimate the gradient. |@docend:minimizer.scipy.gradient|
            hessian: |@doc:minimizer.scipy.hessian| Define the method to use for the hessian computation
                   that the minimizer should use. This can be the
                   hessian provided by the loss itself or
                   method from the minimizer.

                   While the exact gradient can speed up the convergence and is
                   often beneficial, this ain't true for the computation of the
                   (inverse) Hessian matrix.
                   Due to the $n^2$ number of entries (compared to $n$ in the
                   gradient) from the $n$ parameters, this can grow quite
                   large and become computationally expensive.

                   Therefore, many algorithms use an approximated (inverse)
                   Hessian matrix making use of the gradient updates instead
                   of calculating the exact matrix. This turns out to be
                   precise enough and usually considerably speeds up the
                   convergence.

                   The following are possible choices:
                   - If set to ``False`` or ``'zfit'``, the
                     loss hessian (usually using automatic differentiation)
                     will be used;
                     the minimizer won't use an internal algorithm.
                   - A :class:~`scipy.optimize.HessianUpdateStrategy` that holds
                     an approximation of the hessian. For example
                     :class:~`scipy.optimize.BFGS` (which performs usually best)
                     or :class:~`scipy.optimize.SR1`
                     (sometimes unstable updates).
                   - ``True``  (or ``None``; default) tells the minimizer
                     to use its default internal
                     hessian approximation.
                   - arguments ``'2-point'`` and ``'3-point'`` specify which
                     numerical algorithm the minimizer should use in order to
                     estimate the hessian. This is only possible if the
                     gradient is provided by zfit and not an internal numerical
                     method is already used to determine it. |@docend:minimizer.scipy.hessian|
            verbosity: |@doc:minimizer.verbosity| Verbosity of the minimizer.
                A value above 5 starts printing more
                output with a value of 10 printing every
                evaluation of the loss function and gradient. |@docend:minimizer.verbosity|
            maxiter: |@doc:minimizer.maxiter| Approximate number of iterations.
                   This corresponds to roughly the maximum number of
                   evaluations of the `value`, 'gradient` or `hessian`. |@docend:minimizer.maxiter|
            criterion: |@doc:minimizer.criterion| Criterion of the minimum. This is an
                   estimated measure for the distance to the
                   minimum and can include the relative
                   or absolute changes of the parameters,
                   function value, gradients and more.
                   If the value of the criterion is smaller
                   than ``loss.errordef * tol``, the algorithm
                   stopps and it is assumed that the minimum
                   has been found. |@docend:minimizer.criterion|
            strategy: |@doc:minimizer.strategy| A class of type `ZfitStrategy` that takes no
                   input arguments in the init. Determines the behavior of the minimizer in
                   certain situations, most notably when encountering
                   NaNs. It can also implement a callback function. |@docend:minimizer.strategy|
            name: |@doc:minimizer.name| Human readable name of the minimizer. |@docend:minimizer.name|
        """
        options = {}
        if inexact is not None:
            options['inexact'] = inexact

        minimizer_options = {}
        if options:
            minimizer_options['options'] = options

        scipy_tols = {'gtol': None}

        super().__init__(method="trust-constr", internal_tol=scipy_tols, gradient=gradient,
                         hessian=hessian,
                         minimizer_options=minimizer_options, tol=tol, verbosity=verbosity,
                         maxiter=maxiter,
                         strategy=strategy, criterion=criterion, name=name)


ScipyTrustKrylovV1._add_derivative_methods(gradient=['2-point', '3-point',
                                                     # 'cs',  # works badly
                                                     None, True, False, 'zfit'],
                                           hessian=['2-point', '3-point',
                                                    # 'cs',
                                                    scipy.optimize.BFGS, scipy.optimize.SR1,
                                                    None, True, False, 'zfit'])


class ScipyTrustNCGV1(ScipyBaseMinimizer):

    def __init__(self,
                 tol: Optional[float] = None,
                 init_trust_radius: Optional[float] = None,
                 eta: Optional[float] = None,
                 max_trust_radius: Optional[int] = None,
                 gradient: Optional[Union[Callable, str]] = None,
                 hessian: Optional[Union[Callable, str, scipy.optimize.HessianUpdateStrategy]] = SR1,
                 verbosity: Optional[int] = None,
                 maxiter: Optional[Union[int, str]] = None,
                 criterion: Optional[ConvergenceCriterion] = None,
                 strategy: Optional[ZfitStrategy] = None,
                 name: str = "SciPy trust-ncg V1"
                 ) -> None:
        """Local Newton conjugate gradient trust-region algorithm.

        |@doc:minimizer.scipy.info| This implenemtation wraps the minimizers in
        `SciPy optimize <https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.minimize.html>`_. |@docend:minimizer.scipy.info|


        Args:
            tol: |@doc:minimizer.tol| Termination value for the
                   convergence/stopping criterion of the algorithm
                   in order to determine if the minimum has
                   been found. Defaults to 1e-3. |@docend:minimizer.tol|
            eta: |@doc:minimizer.trust.eta| Trust region related acceptance
                   stringency for proposed steps. |@docend:minimizer.trust.eta|
            init_trust_radius: |@doc:minimizer.trust.init_trust_radius| Initial trust-region radius. |@docend:minimizer.trust.init_trust_radius|
            max_trust_radius: |@doc:minimizer.trust.max_trust_radius| Maximum value of the trust-region radius.
                   No steps that are longer than this value will be proposed. |@docend:minimizer.trust.max_trust_radius|
            gradient: |@doc:minimizer.scipy.gradient| Define the method to use for the gradient computation
                   that the minimizer should use. This can be the
                   gradient provided by the loss itself or
                   method from the minimizer.
                   In general, using the zfit provided automatic gradient is
                   more precise and needs less computation time for the
                   evaluation compared to a numerical method.
                   The following are possible choices:
                   - If set to ``False`` or ``'zfit'`` (or ``None``; default), the
                     loss gradient (usually the automatic gradient) will be used;
                     the minimizer won't use an internal algorithm.
                   - ``True`` tells the minimizer to use its default internal
                     gradient estimation.
                   - arguments ``'2-point'`` and ``'3-point'`` specify which
                     numerical algorithm the minimizer should use in order to
                     estimate the gradient. |@docend:minimizer.scipy.gradient|
            hessian: |@doc:minimizer.scipy.hessian| Define the method to use for the hessian computation
                   that the minimizer should use. This can be the
                   hessian provided by the loss itself or
                   method from the minimizer.

                   While the exact gradient can speed up the convergence and is
                   often beneficial, this ain't true for the computation of the
                   (inverse) Hessian matrix.
                   Due to the $n^2$ number of entries (compared to $n$ in the
                   gradient) from the $n$ parameters, this can grow quite
                   large and become computationally expensive.

                   Therefore, many algorithms use an approximated (inverse)
                   Hessian matrix making use of the gradient updates instead
                   of calculating the exact matrix. This turns out to be
                   precise enough and usually considerably speeds up the
                   convergence.

                   The following are possible choices:
                   - If set to ``False`` or ``'zfit'``, the
                     loss hessian (usually using automatic differentiation)
                     will be used;
                     the minimizer won't use an internal algorithm.
                   - A :class:~`scipy.optimize.HessianUpdateStrategy` that holds
                     an approximation of the hessian. For example
                     :class:~`scipy.optimize.BFGS` (which performs usually best)
                     or :class:~`scipy.optimize.SR1`
                     (sometimes unstable updates).
                   - ``True``  (or ``None``; default) tells the minimizer
                     to use its default internal
                     hessian approximation.
                   - arguments ``'2-point'`` and ``'3-point'`` specify which
                     numerical algorithm the minimizer should use in order to
                     estimate the hessian. This is only possible if the
                     gradient is provided by zfit and not an internal numerical
                     method is already used to determine it. |@docend:minimizer.scipy.hessian|
            verbosity: |@doc:minimizer.verbosity| Verbosity of the minimizer.
                A value above 5 starts printing more
                output with a value of 10 printing every
                evaluation of the loss function and gradient. |@docend:minimizer.verbosity|
            maxiter: |@doc:minimizer.maxiter| Approximate number of iterations.
                   This corresponds to roughly the maximum number of
                   evaluations of the `value`, 'gradient` or `hessian`. |@docend:minimizer.maxiter|
            criterion: |@doc:minimizer.criterion| Criterion of the minimum. This is an
                   estimated measure for the distance to the
                   minimum and can include the relative
                   or absolute changes of the parameters,
                   function value, gradients and more.
                   If the value of the criterion is smaller
                   than ``loss.errordef * tol``, the algorithm
                   stopps and it is assumed that the minimum
                   has been found. |@docend:minimizer.criterion|
            strategy: |@doc:minimizer.strategy| A class of type `ZfitStrategy` that takes no
                   input arguments in the init. Determines the behavior of the minimizer in
                   certain situations, most notably when encountering
                   NaNs. It can also implement a callback function. |@docend:minimizer.strategy|
            name: |@doc:minimizer.name| Human readable name of the minimizer. |@docend:minimizer.name|
        """
        options = {}
        if eta is not None:
            options['eta'] = eta
        if max_trust_radius is not None:
            options['max_trust_radius'] = max_trust_radius

        minimizer_options = {}
        if options:
            minimizer_options['options'] = options

        scipy_tols = {'gtol': None}

        super().__init__(method="trust-constr", internal_tol=scipy_tols, gradient=gradient,
                         hessian=hessian,
                         minimizer_options=minimizer_options, tol=tol, verbosity=verbosity,
                         maxiter=maxiter,
                         strategy=strategy, criterion=criterion, name=name)


ScipyTrustNCGV1._add_derivative_methods(gradient=['2-point', '3-point',
                                                  # 'cs'  # works badly
                                                  None, True, False, 'zfit'],
                                        hessian=['2-point', '3-point',
                                                 # 'cs',
                                                 scipy.optimize.BFGS, scipy.optimize.SR1,
                                                 None, True, False, 'zfit'])


class ScipyTrustConstrV1(ScipyBaseMinimizer):
    def __init__(self,
                 tol: Optional[float] = None,
                 init_trust_radius: Optional[int] = None,
                 gradient: Optional[Union[Callable, str]] = None,
                 hessian: Optional[Union[Callable, str, scipy.optimize.HessianUpdateStrategy]] = SR1,
                 verbosity: Optional[int] = None,
                 maxiter: Optional[Union[int, str]] = None,
                 criterion: Optional[ConvergenceCriterion] = None,
                 strategy: Optional[ZfitStrategy] = None,
                 name: str = "SciPy trust-constr V1"
                 ) -> None:
        """Trust-region based local minimizer.

        |@doc:minimizer.scipy.info| This implenemtation wraps the minimizers in
        `SciPy optimize <https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.minimize.html>`_. |@docend:minimizer.scipy.info|


        Args:
            tol: |@doc:minimizer.tol| Termination value for the
                   convergence/stopping criterion of the algorithm
                   in order to determine if the minimum has
                   been found. Defaults to 1e-3. |@docend:minimizer.tol|
            init_trust_radius: |@doc:minimizer.trust.init_trust_radius| Initial trust-region radius. |@docend:minimizer.trust.init_trust_radius|
            gradient: |@doc:minimizer.scipy.gradient| Define the method to use for the gradient computation
                   that the minimizer should use. This can be the
                   gradient provided by the loss itself or
                   method from the minimizer.
                   In general, using the zfit provided automatic gradient is
                   more precise and needs less computation time for the
                   evaluation compared to a numerical method.
                   The following are possible choices:
                   - If set to ``False`` or ``'zfit'`` (or ``None``; default), the
                     loss gradient (usually the automatic gradient) will be used;
                     the minimizer won't use an internal algorithm.
                   - ``True`` tells the minimizer to use its default internal
                     gradient estimation.
                   - arguments ``'2-point'`` and ``'3-point'`` specify which
                     numerical algorithm the minimizer should use in order to
                     estimate the gradient. |@docend:minimizer.scipy.gradient|
            hessian: |@doc:minimizer.scipy.hessian| Define the method to use for the hessian computation
                   that the minimizer should use. This can be the
                   hessian provided by the loss itself or
                   method from the minimizer.

                   While the exact gradient can speed up the convergence and is
                   often beneficial, this ain't true for the computation of the
                   (inverse) Hessian matrix.
                   Due to the $n^2$ number of entries (compared to $n$ in the
                   gradient) from the $n$ parameters, this can grow quite
                   large and become computationally expensive.

                   Therefore, many algorithms use an approximated (inverse)
                   Hessian matrix making use of the gradient updates instead
                   of calculating the exact matrix. This turns out to be
                   precise enough and usually considerably speeds up the
                   convergence.

                   The following are possible choices:
                   - If set to ``False`` or ``'zfit'``, the
                     loss hessian (usually using automatic differentiation)
                     will be used;
                     the minimizer won't use an internal algorithm.
                   - A :class:~`scipy.optimize.HessianUpdateStrategy` that holds
                     an approximation of the hessian. For example
                     :class:~`scipy.optimize.BFGS` (which performs usually best)
                     or :class:~`scipy.optimize.SR1`
                     (sometimes unstable updates).
                   - ``True``  (or ``None``; default) tells the minimizer
                     to use its default internal
                     hessian approximation.
                   - arguments ``'2-point'`` and ``'3-point'`` specify which
                     numerical algorithm the minimizer should use in order to
                     estimate the hessian. This is only possible if the
                     gradient is provided by zfit and not an internal numerical
                     method is already used to determine it. |@docend:minimizer.scipy.hessian|
            verbosity: |@doc:minimizer.verbosity| Verbosity of the minimizer.
                A value above 5 starts printing more
                output with a value of 10 printing every
                evaluation of the loss function and gradient. |@docend:minimizer.verbosity|
            maxiter: |@doc:minimizer.maxiter| Approximate number of iterations.
                   This corresponds to roughly the maximum number of
                   evaluations of the `value`, 'gradient` or `hessian`. |@docend:minimizer.maxiter|
            criterion: |@doc:minimizer.criterion| Criterion of the minimum. This is an
                   estimated measure for the distance to the
                   minimum and can include the relative
                   or absolute changes of the parameters,
                   function value, gradients and more.
                   If the value of the criterion is smaller
                   than ``loss.errordef * tol``, the algorithm
                   stopps and it is assumed that the minimum
                   has been found. |@docend:minimizer.criterion|
            strategy: |@doc:minimizer.strategy| A class of type `ZfitStrategy` that takes no
                   input arguments in the init. Determines the behavior of the minimizer in
                   certain situations, most notably when encountering
                   NaNs. It can also implement a callback function. |@docend:minimizer.strategy|
            name: |@doc:minimizer.name| Human readable name of the minimizer. |@docend:minimizer.name|
        """
        options = {}
        if init_trust_radius is not None:
            options['initial_tr_radius'] = init_trust_radius

        minimizer_options = {}
        if options:
            minimizer_options['options'] = options

        scipy_tols = {'gtol': None, 'xtol': None}

        super().__init__(method="trust-constr", internal_tol=scipy_tols, gradient=gradient,
                         hessian=hessian,
                         minimizer_options=minimizer_options, tol=tol, verbosity=verbosity,
                         maxiter=maxiter,
                         strategy=strategy, criterion=criterion, name=name)


ScipyTrustConstrV1._add_derivative_methods(gradient=['2-point', '3-point',
                                                     # 'cs',  # works badly
                                                     None, True, False, 'zfit'],
                                           hessian=['2-point', '3-point',
                                                    # 'cs',
                                                    scipy.optimize.BFGS, scipy.optimize.SR1,
                                                    None, True, False, 'zfit'])


class ScipyNewtonCGV1(ScipyBaseMinimizer):

    @warn_experimental_feature
    def __init__(self,
                 tol: Optional[float] = None,
                 gradient: Optional[Union[Callable, str]] = None,
                 hessian: Optional[Union[Callable, str, scipy.optimize.HessianUpdateStrategy]] = 'zfit',
                 verbosity: Optional[int] = None,
                 maxiter: Optional[Union[int, str]] = None,
                 criterion: Optional[ConvergenceCriterion] = None,
                 strategy: Optional[ZfitStrategy] = None,
                 name: str = "SciPy Newton-CG V1"
                 ) -> None:
        """WARNING! This algorithm seems unstable and may does not perform well!

        |@doc:minimizer.scipy.info| This implenemtation wraps the minimizers in
        `SciPy optimize <https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.minimize.html>`_. |@docend:minimizer.scipy.info|


        Args:
            tol: |@doc:minimizer.tol| Termination value for the
                   convergence/stopping criterion of the algorithm
                   in order to determine if the minimum has
                   been found. Defaults to 1e-3. |@docend:minimizer.tol|
            gradient: |@doc:minimizer.scipy.gradient| Define the method to use for the gradient computation
                   that the minimizer should use. This can be the
                   gradient provided by the loss itself or
                   method from the minimizer.
                   In general, using the zfit provided automatic gradient is
                   more precise and needs less computation time for the
                   evaluation compared to a numerical method.
                   The following are possible choices:
                   - If set to ``False`` or ``'zfit'`` (or ``None``; default), the
                     loss gradient (usually the automatic gradient) will be used;
                     the minimizer won't use an internal algorithm.
                   - ``True`` tells the minimizer to use its default internal
                     gradient estimation.
                   - arguments ``'2-point'`` and ``'3-point'`` specify which
                     numerical algorithm the minimizer should use in order to
                     estimate the gradient. |@docend:minimizer.scipy.gradient|
            hessian: |@doc:minimizer.scipy.hessian| Define the method to use for the hessian computation
                   that the minimizer should use. This can be the
                   hessian provided by the loss itself or
                   method from the minimizer.

                   While the exact gradient can speed up the convergence and is
                   often beneficial, this ain't true for the computation of the
                   (inverse) Hessian matrix.
                   Due to the $n^2$ number of entries (compared to $n$ in the
                   gradient) from the $n$ parameters, this can grow quite
                   large and become computationally expensive.

                   Therefore, many algorithms use an approximated (inverse)
                   Hessian matrix making use of the gradient updates instead
                   of calculating the exact matrix. This turns out to be
                   precise enough and usually considerably speeds up the
                   convergence.

                   The following are possible choices:
                   - If set to ``False`` or ``'zfit'``, the
                     loss hessian (usually using automatic differentiation)
                     will be used;
                     the minimizer won't use an internal algorithm.
                   - A :class:~`scipy.optimize.HessianUpdateStrategy` that holds
                     an approximation of the hessian. For example
                     :class:~`scipy.optimize.BFGS` (which performs usually best)
                     or :class:~`scipy.optimize.SR1`
                     (sometimes unstable updates).
                   - ``True``  (or ``None``; default) tells the minimizer
                     to use its default internal
                     hessian approximation.
                   - arguments ``'2-point'`` and ``'3-point'`` specify which
                     numerical algorithm the minimizer should use in order to
                     estimate the hessian. This is only possible if the
                     gradient is provided by zfit and not an internal numerical
                     method is already used to determine it. |@docend:minimizer.scipy.hessian|
            verbosity: |@doc:minimizer.verbosity| Verbosity of the minimizer.
                A value above 5 starts printing more
                output with a value of 10 printing every
                evaluation of the loss function and gradient. |@docend:minimizer.verbosity|
            maxiter: |@doc:minimizer.maxiter| Approximate number of iterations.
                   This corresponds to roughly the maximum number of
                   evaluations of the `value`, 'gradient` or `hessian`. |@docend:minimizer.maxiter|
            criterion: |@doc:minimizer.criterion| Criterion of the minimum. This is an
                   estimated measure for the distance to the
                   minimum and can include the relative
                   or absolute changes of the parameters,
                   function value, gradients and more.
                   If the value of the criterion is smaller
                   than ``loss.errordef * tol``, the algorithm
                   stopps and it is assumed that the minimum
                   has been found. |@docend:minimizer.criterion|
            strategy: |@doc:minimizer.strategy| A class of type `ZfitStrategy` that takes no
                   input arguments in the init. Determines the behavior of the minimizer in
                   certain situations, most notably when encountering
                   NaNs. It can also implement a callback function. |@docend:minimizer.strategy|
            name: |@doc:minimizer.name| Human readable name of the minimizer. |@docend:minimizer.name|
        """
        options = {}

        minimizer_options = {}
        if options:
            minimizer_options['options'] = options

        scipy_tols = {'xtol': None}

        method = "Newton-CG"
        super().__init__(method=method, internal_tol=scipy_tols, gradient=gradient, hessian=hessian,
                         minimizer_options=minimizer_options, tol=tol, verbosity=verbosity,
                         maxiter=maxiter,
                         strategy=strategy, criterion=criterion, name=name)


ScipyNewtonCGV1._add_derivative_methods(gradient=['2-point', '3-point',
                                                  # 'cs',  # works badly
                                                  None, True, False, 'zfit'],
                                        hessian=['2-point', '3-point',
                                                 # 'cs',
                                                 scipy.optimize.BFGS, scipy.optimize.SR1,
                                                 None, True, False, 'zfit'])


class ScipyTruncNCV1(ScipyBaseMinimizer):
    def __init__(self, tol: Optional[float] = None,
                 maxcg: Optional[int] = None,  # maxCGit
                 maxls: Optional[int] = None,  # stepmx
                 eta: Optional[float] = None,
                 rescale: Optional[float] = None,
                 gradient: Optional[Union[Callable, str]] = None,
                 verbosity: Optional[int] = None,
                 maxiter: Optional[Union[int, str]] = None,
                 criterion: Optional[ConvergenceCriterion] = None,
                 strategy: Optional[ZfitStrategy] = None,
                 name: str = "SciPy Truncated Newton Conjugate V1"
                 ) -> None:
        """Local, gradient based minimization algorithm using a truncated Newton method.

        `Truncated Newton Methods <https://en.wikipedia.org/wiki/Truncated_Newton_method>`_ provide
        a hessian-free way of optimization.

        |@doc:minimizer.scipy.info| This implenemtation wraps the minimizers in
        `SciPy optimize <https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.minimize.html>`_. |@docend:minimizer.scipy.info|


        Args:
            tol: |@doc:minimizer.tol| Termination value for the
                   convergence/stopping criterion of the algorithm
                   in order to determine if the minimum has
                   been found. Defaults to 1e-3. |@docend:minimizer.tol|
            maxcg: Maximum number of conjugate gradient evaluations (hessian*vector evaluations)
                per main iteration. If maxCGit == 0, the direction chosen is -gradient if maxCGit < 0, maxCGit is set to max(1,min(50,n/2)). Defaults to -1.
            maxls: Maximum step for the line search. May be increased during call.
                If too small, it will be set to 10.0. Defaults to 0.
            eta: Severity of the line search, should be between 0 and 1.
            rescale: Scaling factor (in log10) used to trigger loss value rescaling.
                 If set to 0, rescale at each iteration.
                 If it is a very large value, never rescale.
            gradient: |@doc:minimizer.scipy.gradient| Define the method to use for the gradient computation
                   that the minimizer should use. This can be the
                   gradient provided by the loss itself or
                   method from the minimizer.
                   In general, using the zfit provided automatic gradient is
                   more precise and needs less computation time for the
                   evaluation compared to a numerical method.
                   The following are possible choices:
                   - If set to ``False`` or ``'zfit'`` (or ``None``; default), the
                     loss gradient (usually the automatic gradient) will be used;
                     the minimizer won't use an internal algorithm.
                   - ``True`` tells the minimizer to use its default internal
                     gradient estimation.
                   - arguments ``'2-point'`` and ``'3-point'`` specify which
                     numerical algorithm the minimizer should use in order to
                     estimate the gradient. |@docend:minimizer.scipy.gradient|
            verbosity: |@doc:minimizer.verbosity| Verbosity of the minimizer.
                A value above 5 starts printing more
                output with a value of 10 printing every
                evaluation of the loss function and gradient. |@docend:minimizer.verbosity|
            maxiter: |@doc:minimizer.maxiter| Approximate number of iterations.
                   This corresponds to roughly the maximum number of
                   evaluations of the `value`, 'gradient` or `hessian`. |@docend:minimizer.maxiter|
            criterion: |@doc:minimizer.criterion| Criterion of the minimum. This is an
                   estimated measure for the distance to the
                   minimum and can include the relative
                   or absolute changes of the parameters,
                   function value, gradients and more.
                   If the value of the criterion is smaller
                   than ``loss.errordef * tol``, the algorithm
                   stopps and it is assumed that the minimum
                   has been found. |@docend:minimizer.criterion|
            strategy: |@doc:minimizer.strategy| A class of type `ZfitStrategy` that takes no
                   input arguments in the init. Determines the behavior of the minimizer in
                   certain situations, most notably when encountering
                   NaNs. It can also implement a callback function. |@docend:minimizer.strategy|
            name: |@doc:minimizer.name| Human readable name of the minimizer. |@docend:minimizer.name|
        """
        options = {}
        if maxcg is not None:
            options['maxiter_cg'] = maxcg
        if eta is not None:
            options['eta'] = eta
        if maxls is not None:
            options['maxstep_ls'] = maxls
        if rescale is not None:
            options['rescale'] = rescale

        options['maxfun'] = None  # in order to use maxiter
        minimizer_options = {}
        if options:
            minimizer_options['options'] = options

        scipy_tols = {'xtol': None, 'ftol': None, 'gtol': None}

        method = "TNC"
        super().__init__(method=method, tol=tol, verbosity=verbosity,
                         strategy=strategy, gradient=gradient, hessian=NOT_SUPPORTED,
                         criterion=criterion, internal_tol=scipy_tols,
                         maxiter=maxiter,
                         minimizer_options=minimizer_options,
                         name=name)


ScipyTruncNCV1._add_derivative_methods(gradient=['2-point', '3-point',
                                                 # 'cs'  # works badly
                                                 None, True, False, 'zfit'])


class ScipyDoglegV1(ScipyBaseMinimizer):
    def __init__(self,
                 tol: Optional[float] = None,
                 init_trust_radius: Optional[int] = None,
                 eta: Optional[float] = None,
                 max_trust_radius: Optional[int] = None,
                 verbosity: Optional[int] = None,
                 maxiter: Optional[Union[int, str]] = None,
                 criterion: Optional[ConvergenceCriterion] = None,
                 strategy: Optional[ZfitStrategy] = None,
                 name: str = "SciPy Dogleg V1"
                 ) -> None:
        """This minimizer requires the hessian and gradient to be provided by the loss itself.

        Args:
            tol: |@doc:minimizer.tol| Termination value for the
                   convergence/stopping criterion of the algorithm
                   in order to determine if the minimum has
                   been found. Defaults to 1e-3. |@docend:minimizer.tol|
            eta: |@doc:minimizer.trust.eta| Trust region related acceptance
                   stringency for proposed steps. |@docend:minimizer.trust.eta|
            init_trust_radius: |@doc:minimizer.trust.init_trust_radius| Initial trust-region radius. |@docend:minimizer.trust.init_trust_radius|
            max_trust_radius: |@doc:minimizer.trust.max_max_trust_radius||@docend:minimizer.trust.max_max_trust_radius|
            verbosity: |@doc:minimizer.verbosity| Verbosity of the minimizer.
                A value above 5 starts printing more
                output with a value of 10 printing every
                evaluation of the loss function and gradient. |@docend:minimizer.verbosity|
            maxiter: |@doc:minimizer.maxiter| Approximate number of iterations.
                   This corresponds to roughly the maximum number of
                   evaluations of the `value`, 'gradient` or `hessian`. |@docend:minimizer.maxiter|
            criterion: |@doc:minimizer.criterion| Criterion of the minimum. This is an
                   estimated measure for the distance to the
                   minimum and can include the relative
                   or absolute changes of the parameters,
                   function value, gradients and more.
                   If the value of the criterion is smaller
                   than ``loss.errordef * tol``, the algorithm
                   stopps and it is assumed that the minimum
                   has been found. |@docend:minimizer.criterion|
            strategy: |@doc:minimizer.strategy| A class of type `ZfitStrategy` that takes no
                   input arguments in the init. Determines the behavior of the minimizer in
                   certain situations, most notably when encountering
                   NaNs. It can also implement a callback function. |@docend:minimizer.strategy|
            name: |@doc:minimizer.name| Human readable name of the minimizer. |@docend:minimizer.name|
        """
        options = {}
        if init_trust_radius is not None:
            options['initial_tr_radius'] = init_trust_radius
        if eta is not None:
            options['eta'] = eta
        if max_trust_radius is not None:
            options['max_trust_radius'] = max_trust_radius

        minimizer_options = {}
        if options:
            minimizer_options['options'] = options

        scipy_tols = {'gtol': None}

        super().__init__(method="dogleg", internal_tol=scipy_tols,
                         gradient='zfit',
                         hessian='zfit',
                         minimizer_options=minimizer_options, tol=tol, verbosity=verbosity,
                         maxiter=maxiter,
                         strategy=strategy, criterion=criterion, name=name)


ScipyDoglegV1._add_derivative_methods(gradient=['zfit'], hessian=['zfit'])

class ScipyPowellV1(ScipyBaseMinimizer):
    def __init__(self,
                 tol: Optional[float] = None,
                 verbosity: Optional[int] = None,
                 maxiter: Optional[Union[int, str]] = None,
                 criterion: Optional[ConvergenceCriterion] = None,
                 strategy: Optional[ZfitStrategy] = None,
                 name: str = "SciPy Powell V1"
                 ) -> None:
        """Local minimizer using the modified Powell algorithm.

        |@doc:minimizer.scipy.info| This implenemtation wraps the minimizers in
        `SciPy optimize <https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.minimize.html>`_. |@docend:minimizer.scipy.info|

        Args:
            tol: |@doc:minimizer.tol| Termination value for the
                   convergence/stopping criterion of the algorithm
                   in order to determine if the minimum has
                   been found. Defaults to 1e-3. |@docend:minimizer.tol|
            verbosity: |@doc:minimizer.verbosity| Verbosity of the minimizer.
                A value above 5 starts printing more
                output with a value of 10 printing every
                evaluation of the loss function and gradient. |@docend:minimizer.verbosity|
            maxiter: |@doc:minimizer.maxiter| Approximate number of iterations.
                   This corresponds to roughly the maximum number of
                   evaluations of the `value`, 'gradient` or `hessian`. |@docend:minimizer.maxiter|
            criterion: |@doc:minimizer.criterion| Criterion of the minimum. This is an
                   estimated measure for the distance to the
                   minimum and can include the relative
                   or absolute changes of the parameters,
                   function value, gradients and more.
                   If the value of the criterion is smaller
                   than ``loss.errordef * tol``, the algorithm
                   stopps and it is assumed that the minimum
                   has been found. |@docend:minimizer.criterion|
            strategy: |@doc:minimizer.strategy| A class of type `ZfitStrategy` that takes no
                   input arguments in the init. Determines the behavior of the minimizer in
                   certain situations, most notably when encountering
                   NaNs. It can also implement a callback function. |@docend:minimizer.strategy|
            name: |@doc:minimizer.name| Human readable name of the minimizer. |@docend:minimizer.name|
        """
        options = {}
        minimizer_options = {}
        if options:
            minimizer_options['options'] = options

        scipy_tols = {'xtol': None, 'ftol': None}

        method = "Powell"
        super().__init__(method=method, internal_tol=scipy_tols, gradient=NOT_SUPPORTED,
                         hessian=NOT_SUPPORTED, minimizer_options=minimizer_options, tol=tol,
                         maxiter=maxiter,
                         verbosity=verbosity, strategy=strategy, criterion=criterion, name=name)


class ScipySLSQPV1(ScipyBaseMinimizer):
    def __init__(self,
                 tol: Optional[float] = None,
                 gradient: Optional[Union[Callable, str]] = None,
                 verbosity: Optional[int] = None,
                 maxiter: Optional[Union[int, str]] = None,
                 criterion: Optional[ConvergenceCriterion] = None,
                 strategy: Optional[ZfitStrategy] = None,
                 name: str = "SciPy SLSQP V1"
                 ) -> None:
        """Local, gradient-based minimizer using tho  Sequential Least Squares Programming algorithm.name.

         `Sequential Least Squares Programming <https://en.wikipedia.org/wiki/Sequential_quadratic_programming>`_
         is an iterative method for nonlinear parameter optimization.

         |@doc:minimizer.scipy.info| This implenemtation wraps the minimizers in
        `SciPy optimize <https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.minimize.html>`_. |@docend:minimizer.scipy.info|

        Args:
            tol: |@doc:minimizer.tol| Termination value for the
                   convergence/stopping criterion of the algorithm
                   in order to determine if the minimum has
                   been found. Defaults to 1e-3. |@docend:minimizer.tol|
            gradient: |@doc:minimizer.scipy.gradient| Define the method to use for the gradient computation
                   that the minimizer should use. This can be the
                   gradient provided by the loss itself or
                   method from the minimizer.
                   In general, using the zfit provided automatic gradient is
                   more precise and needs less computation time for the
                   evaluation compared to a numerical method.
                   The following are possible choices:
                   - If set to ``False`` or ``'zfit'`` (or ``None``; default), the
                     loss gradient (usually the automatic gradient) will be used;
                     the minimizer won't use an internal algorithm.
                   - ``True`` tells the minimizer to use its default internal
                     gradient estimation.
                   - arguments ``'2-point'`` and ``'3-point'`` specify which
                     numerical algorithm the minimizer should use in order to
                     estimate the gradient. |@docend:minimizer.scipy.gradient|
            verbosity: |@doc:minimizer.verbosity| Verbosity of the minimizer.
                A value above 5 starts printing more
                output with a value of 10 printing every
                evaluation of the loss function and gradient. |@docend:minimizer.verbosity|
            maxiter: |@doc:minimizer.maxiter| Approximate number of iterations.
                   This corresponds to roughly the maximum number of
                   evaluations of the `value`, 'gradient` or `hessian`. |@docend:minimizer.maxiter|
            criterion: |@doc:minimizer.criterion| Criterion of the minimum. This is an
                   estimated measure for the distance to the
                   minimum and can include the relative
                   or absolute changes of the parameters,
                   function value, gradients and more.
                   If the value of the criterion is smaller
                   than ``loss.errordef * tol``, the algorithm
                   stopps and it is assumed that the minimum
                   has been found. |@docend:minimizer.criterion|
            strategy: |@doc:minimizer.strategy| A class of type `ZfitStrategy` that takes no
                   input arguments in the init. Determines the behavior of the minimizer in
                   certain situations, most notably when encountering
                   NaNs. It can also implement a callback function. |@docend:minimizer.strategy|
            name: |@doc:minimizer.name| Human readable name of the minimizer. |@docend:minimizer.name|
        """
        options = {}
        minimizer_options = {}
        if options:
            minimizer_options['options'] = options

        scipy_tols = {'ftol': None}

        method = "SLSQP"
        super().__init__(method=method, internal_tol=scipy_tols, gradient=gradient, hessian=NOT_SUPPORTED,
                         minimizer_options=minimizer_options, tol=tol, verbosity=verbosity,
                         maxiter=maxiter,
                         strategy=strategy, criterion=criterion, name=name)


ScipySLSQPV1._add_derivative_methods(gradient=['2-point', '3-point',
                                               # 'cs',  # works badly
                                               None, True, False, 'zfit'])


class ScipyNelderMeadV1(ScipyBaseMinimizer):
    def __init__(self,
                 tol: Optional[float] = None,
                 adaptive: Optional[bool] = True,
                 verbosity: Optional[int] = None,
                 maxiter: Optional[Union[int, str]] = None,
                 criterion: Optional[ConvergenceCriterion] = None,
                 strategy: Optional[ZfitStrategy] = None,
                 name: str = "SciPy Nelder-Mead V1"
                 ) -> None:
        """Local gradient-free dowhhill simplex method.py.

        `Nelder-Mead <https://en.wikipedia.org/wiki/Nelder%E2%80%93Mead_method>`_
         is a gradient-free method to minimize an objective function. It's performance is
         usually inferior to gradient based algorithms.

        |@doc:minimizer.scipy.info| This implenemtation wraps the minimizers in
        `SciPy optimize <https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.minimize.html>`_. |@docend:minimizer.scipy.info|


        Args:
            tol: |@doc:minimizer.tol| Termination value for the
                   convergence/stopping criterion of the algorithm
                   in order to determine if the minimum has
                   been found. Defaults to 1e-3. |@docend:minimizer.tol|
            adaptive:
            verbosity: |@doc:minimizer.verbosity| Verbosity of the minimizer.
                A value above 5 starts printing more
                output with a value of 10 printing every
                evaluation of the loss function and gradient. |@docend:minimizer.verbosity|
            maxiter: |@doc:minimizer.maxiter| Approximate number of iterations.
                   This corresponds to roughly the maximum number of
                   evaluations of the `value`, 'gradient` or `hessian`. |@docend:minimizer.maxiter|
            criterion: |@doc:minimizer.criterion| Criterion of the minimum. This is an
                   estimated measure for the distance to the
                   minimum and can include the relative
                   or absolute changes of the parameters,
                   function value, gradients and more.
                   If the value of the criterion is smaller
                   than ``loss.errordef * tol``, the algorithm
                   stopps and it is assumed that the minimum
                   has been found. |@docend:minimizer.criterion|
            strategy: |@doc:minimizer.strategy| A class of type `ZfitStrategy` that takes no
                   input arguments in the init. Determines the behavior of the minimizer in
                   certain situations, most notably when encountering
                   NaNs. It can also implement a callback function. |@docend:minimizer.strategy|
            name: |@doc:minimizer.name| Human readable name of the minimizer. |@docend:minimizer.name|
        """
        options = {}
        minimizer_options = {}

        if adaptive is not None:
            options['adaptive'] = adaptive
        if options:
            minimizer_options['options'] = options

        scipy_tols = {'fatol': None, 'xatol': None}

        method = "Nelder-Mead"
        super().__init__(method=method, internal_tol=scipy_tols, gradient=NOT_SUPPORTED,
                         hessian=NOT_SUPPORTED, minimizer_options=minimizer_options, tol=tol,
                         maxiter=maxiter,
                         verbosity=verbosity, strategy=strategy, criterion=criterion, name=name)


def combine_optimize_results(results):
    if len(results) == 1:
        return results[0]
    result = results[-1]
    for field in ['nfev', 'njev', 'nhev', 'nit']:
        if field in result:
            result[field] = sum(res[field] for res in results)
    return result
