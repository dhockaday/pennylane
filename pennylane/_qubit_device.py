# Copyright 2018-2021 Xanadu Quantum Technologies Inc.

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
This module contains the :class:`QubitDevice` abstract base class.
"""


# For now, arguments may be different from the signatures provided in Device
# e.g. instead of expval(self, observable, wires, par) have expval(self, observable)
# pylint: disable=arguments-differ, abstract-method, no-value-for-parameter,too-many-instance-attributes,too-many-branches, no-member, bad-option-value, arguments-renamed
import abc
import contextlib
import itertools
import warnings
from typing import Union

import numpy as np

import pennylane as qml
from pennylane import Device, DeviceError
from pennylane.interfaces import set_shots
from pennylane.math import multiply as qmlmul
from pennylane.math import sum as qmlsum
from pennylane.measurements import (
    AllCounts,
    ClassicalShadowMP,
    Counts,
    CountsMP,
    Expectation,
    ExpectationMP,
    MeasurementProcess,
    MeasurementTransform,
    MutualInfo,
    MutualInfoMP,
    Probability,
    ProbabilityMP,
    Sample,
    SampleMeasurement,
    SampleMP,
    Shadow,
    ShadowExpval,
    ShadowExpvalMP,
    State,
    StateMeasurement,
    StateMP,
    Variance,
    VarianceMP,
    VnEntropy,
    VnEntropyMP,
)
from pennylane.operation import operation_derivative
from pennylane.tape import QuantumScript
from pennylane.wires import Wires


class QubitDevice(Device):
    """Abstract base class for PennyLane qubit devices.

    The following abstract method **must** be defined:

    * :meth:`~.apply`: append circuit operations, compile the circuit (if applicable),
      and perform the quantum computation.

    Devices that generate their own samples (such as hardware) may optionally
    overwrite :meth:`~.probabilty`. This method otherwise automatically
    computes the probabilities from the generated samples, and **must**
    overwrite the following method:

    * :meth:`~.generate_samples`: Generate samples from the device from the
      exact or approximate probability distribution.

    Analytic devices **must** overwrite the following method:

    * :meth:`~.analytic_probability`: returns the probability or marginal probability from the
      device after circuit execution. :meth:`~.marginal_prob` may be used here.

    This device contains common utility methods for qubit-based devices. These
    do not need to be overwritten. Utility methods include:

    * :meth:`~.expval`, :meth:`~.var`, :meth:`~.sample`: return expectation values,
      variances, and samples of observables after the circuit has been rotated
      into the observable eigenbasis.

    Args:
        wires (int, Iterable[Number, str]]): Number of subsystems represented by the device,
            or iterable that contains unique labels for the subsystems as numbers (i.e., ``[-1, 0, 2]``)
            or strings (``['ancilla', 'q1', 'q2']``). Default 1 if not specified.
        shots (None, int, list[int]): Number of circuit evaluations/random samples used to estimate
            expectation values of observables. If ``None``, the device calculates probability, expectation values,
            and variances analytically. If an integer, it specifies the number of samples to estimate these quantities.
            If a list of integers is passed, the circuit evaluations are batched over the list of shots.
        r_dtype: Real floating point precision type.
        c_dtype: Complex floating point precision type.
    """

    # pylint: disable=too-many-public-methods

    _asarray = staticmethod(np.asarray)
    _dot = staticmethod(np.dot)
    _abs = staticmethod(np.abs)
    _reduce_sum = staticmethod(lambda array, axes: np.sum(array, axis=tuple(axes)))
    _reshape = staticmethod(np.reshape)
    _flatten = staticmethod(lambda array: array.flatten())
    _gather = staticmethod(
        lambda array, indices, axis=0: array[:, indices] if axis == 1 else array[indices]
    )  # Make sure to only use _gather with axis=0 or axis=1
    _einsum = staticmethod(np.einsum)
    _cast = staticmethod(np.asarray)
    _transpose = staticmethod(np.transpose)
    _tensordot = staticmethod(np.tensordot)
    _conj = staticmethod(np.conj)
    _imag = staticmethod(np.imag)
    _roll = staticmethod(np.roll)
    _stack = staticmethod(np.stack)
    _outer = staticmethod(np.outer)
    _diag = staticmethod(np.diag)
    _real = staticmethod(np.real)
    _size = staticmethod(np.size)
    _ndim = staticmethod(np.ndim)

    @staticmethod
    def _scatter(indices, array, new_dimensions):
        new_array = np.zeros(new_dimensions, dtype=array.dtype.type)
        new_array[indices] = array
        return new_array

    @staticmethod
    def _const_mul(constant, array):
        """Data type preserving multiply operation"""
        return qmlmul(constant, array, dtype=array.dtype)

    def _permute_wires(self, observable):
        r"""Given an observable which acts on multiple wires, permute the wires to
          be consistent with the device wire order.

          Suppose we are given an observable :math:`\hat{O} = \Identity \otimes \Identity \otimes \hat{Z}`.
          This observable can be represented in many ways:

        .. code-block:: python

              O_1 = qml.Identity(wires=0) @ qml.Identity(wires=1) @ qml.PauliZ(wires=2)
              O_2 = qml.PauliZ(wires=2) @ qml.Identity(wires=0) @ qml.Identity(wires=1)

          Notice that while the explicit tensor product matrix representation of :code:`O_1` and :code:`O_2` is
          different, the underlying operator is identical due to the wire labelling (assuming the labels in
          ascending order are {0,1,2}). If we wish to compute the expectation value of such an observable, we must
          ensure it is identical in both cases. To facilitate this, we permute the wires in our state vector such
          that they are consistent with this swapping of order in the tensor observable.

        .. code-block:: python

              >>> print(O_1.wires)
              <Wires = [0, 1, 2]>
              >>> print(O_2.wires)
              <Wires = [2, 0, 1]>

          We might naively think that we must permute our state vector to match the wire order of our tensor observable.
          We must be careful and realize that the wire order of the terms in the tensor observable DOES NOT match the
          permutation of the terms themselves. As an example we directly compare :code:`O_1` and :code:`O_2`:

          The first term in :code:`O_1` (:code:`qml.Identity(wires=0)`) became the second term in :code:`O_2`.
          By similar comparison we see that each term in the tensor product was shifted one position forward
          (i.e 0 --> 1, 1 --> 2, 2 --> 0). The wires in our permuted quantum state should follow their respective
          terms in the tensor product observable.

          Thus, the correct wire ordering should be :code:`permuted_wires = <Wires = [1, 2, 0]>`. But if we had
          taken the naive approach we would have permuted our state according to
          :code:`permuted_wires = <Wires = [2, 0, 1]>` which is NOT correct.

          This function uses the observable wires and the global device wire ordering in order to determine the
          permutation of the wires in the observable required such that if our quantum state vector is
          permuted accordingly then the amplitudes of the state will match the matrix representation of the observable.

          Args:
              observable (Observable): the observable whose wires are to be permuted.

          Returns:
              permuted_wires (Wires): permuted wires object
        """
        ordered_obs_wire_lst = self.order_wires(
            observable.wires
        ).tolist()  # order according to device wire order

        mapped_wires = self.map_wires(observable.wires)
        if isinstance(mapped_wires, Wires):
            # by default this should be a Wires obj, but it is overwritten to list object in default.qubit
            mapped_wires = mapped_wires.tolist()

        permutation = np.argsort(mapped_wires)  # extract permutation via argsort

        return Wires([ordered_obs_wire_lst[index] for index in permutation])

    observables = {
        "PauliX",
        "PauliY",
        "PauliZ",
        "Hadamard",
        "Hermitian",
        "Identity",
        "Projector",
        "Sum",
        "Sprod",
        "Prod",
    }

    def __init__(
        self, wires=1, shots=None, *, r_dtype=np.float64, c_dtype=np.complex128, analytic=None
    ):
        super().__init__(wires=wires, shots=shots, analytic=analytic)

        if "float" not in str(r_dtype):
            raise DeviceError("Real datatype must be a floating point type.")
        if "complex" not in str(c_dtype):
            raise DeviceError("Complex datatype must be a complex floating point type.")

        self.C_DTYPE = c_dtype
        self.R_DTYPE = r_dtype

        self._samples = None
        """None or array[int]: stores the samples generated by the device
        *after* rotation to diagonalize the observables."""

    @classmethod
    def capabilities(cls):

        capabilities = super().capabilities().copy()
        capabilities.update(
            model="qubit",
            supports_broadcasting=False,
            supports_finite_shots=True,
            supports_tensor_observables=True,
            returns_probs=True,
        )
        return capabilities

    def reset(self):
        """Reset the backend state.

        After the reset, the backend should be as if it was just constructed.
        Most importantly the quantum state is reset to its initial value.
        """
        self._samples = None

    def _collect_shotvector_results(self, circuit, counts_exist):
        """Obtain and process statistics when using a shot vector.
        This routine is part of the ``execute()`` method."""

        if self._ndim(self._samples) == 3:
            raise NotImplementedError(
                "Parameter broadcasting when using a shot vector is not supported yet."
            )
        results = []
        s1 = 0

        for shot_tuple in self._shot_vector:
            s2 = s1 + np.prod(shot_tuple)
            r = self.statistics(circuit, shot_range=[s1, s2], bin_size=shot_tuple.shots)

            if qml.math.get_interface(*r) == "jax":  # pylint: disable=protected-access
                r = r[0]
            elif not counts_exist:
                # Measurement types except for Counts
                r = qml.math.squeeze(r)

            if counts_exist:

                # This happens when at least one measurement type is Counts
                for result_group in r:
                    if isinstance(result_group, list):
                        # List that contains one or more dictionaries
                        results.extend(result_group)
                    else:
                        # Other measurement results
                        results.append(result_group.T)

            elif shot_tuple.copies > 1:
                results.extend(r.T)
            else:
                results.append(r.T)

            s1 = s2

        multiple_sampled_jobs = circuit.is_sampled and self._has_partitioned_shots()
        if not multiple_sampled_jobs and not counts_exist:
            # Can only stack single element outputs
            results = self._stack(results)

        return results

    def _execute_new(self, circuit, **kwargs):
        """New execute (update of return type) function, it executes a queue of quantum operations on the device and
        then measure the given observables. More case will be added in future PRs, for the moment it only supports
        measurements without shots.

        For plugin developers: instead of overwriting this, consider
        implementing a suitable subset of

        * :meth:`apply`

        * :meth:`~.generate_samples`

        * :meth:`~.probability`

        Additional keyword arguments may be passed to the this method
        that can be utilised by :meth:`apply`. An example would be passing
        the ``QNode`` hash that can be used later for parametric compilation.

        Args:
            circuit (~.tapes.QuantumTape): circuit to execute on the device

        Raises:
            QuantumFunctionError: if the value of :attr:`~.Observable.return_type` is not supported

        Returns:
            array[float]: measured value(s)
        """
        self.check_validity(circuit.operations, circuit.observables)

        # apply all circuit operations
        self.apply(circuit.operations, rotations=circuit.diagonalizing_gates, **kwargs)

        # generate computational basis samples
        if self.shots is not None:
            self._samples = self.generate_samples()

        # compute the required statistics
        if self._shot_vector is not None:

            results = self.shot_vec_statistics(circuit)

        else:
            results = self._statistics_new(circuit)
            single_measurement = len(circuit.measurements) == 1

            results = results[0] if single_measurement else tuple(results)
        # increment counter for number of executions of qubit device
        self._num_executions += 1

        if self.tracker.active:
            self.tracker.update(executions=1, shots=self._shots, results=results)
            self.tracker.record()

        return results

    def execute(self, circuit: QuantumScript, **kwargs):
        """Execute a queue of quantum operations on the device and then
        measure the given observables.

        For plugin developers: instead of overwriting this, consider
        implementing a suitable subset of

        * :meth:`apply`

        * :meth:`~.generate_samples`

        * :meth:`~.probability`

        Additional keyword arguments may be passed to the this method
        that can be utilised by :meth:`apply`. An example would be passing
        the ``QNode`` hash that can be used later for parametric compilation.

        Args:
            circuit (~.tapes.QuantumTape): circuit to execute on the device

        Raises:
            QuantumFunctionError: if the value of :attr:`~.Observable.return_type` is not supported

        Returns:
            array[float]: measured value(s)
        """
        if qml.active_return():
            return self._execute_new(circuit, **kwargs)

        self.check_validity(circuit.operations, circuit.observables)

        # apply all circuit operations
        self.apply(circuit.operations, rotations=circuit.diagonalizing_gates, **kwargs)

        # generate computational basis samples
        if self.shots is not None or circuit.is_sampled:
            self._samples = self.generate_samples()

        measurements = circuit.measurements
        counts_exist = any(isinstance(m, CountsMP) for m in measurements)

        # compute the required statistics
        if not self.analytic and self._shot_vector is not None:
            results = self._collect_shotvector_results(circuit, counts_exist)
        else:
            results = self.statistics(circuit=circuit)

        if not circuit.is_sampled:

            if len(measurements) == 1:
                if isinstance(measurements[0], StateMP):
                    # State: assumed to only be allowed if it's the only measurement
                    results = self._asarray(results, dtype=self.C_DTYPE)
                else:
                    # Measurements with expval, var or probs
                    with contextlib.suppress(TypeError):
                        # Feature for returning custom objects: if the type cannot be cast to float then we can still allow it as an output
                        results = self._asarray(results, dtype=self.R_DTYPE)
            elif all(isinstance(m, (ExpectationMP, VarianceMP)) for m in measurements):
                # Measurements with expval or var
                results = self._asarray(results, dtype=self.R_DTYPE)
            elif not counts_exist:
                # all the other cases except any counts
                results = self._asarray(results)

        elif circuit.all_sampled and not self._has_partitioned_shots() and not counts_exist:
            results = self._asarray(results)
        else:
            results = tuple(
                r if isinstance(r, dict) else qml.math.squeeze(self._asarray(r)) for r in results
            )

        # increment counter for number of executions of qubit device
        self._num_executions += 1

        if self.tracker.active:
            self.tracker.update(executions=1, shots=self._shots, results=results)
            self.tracker.record()

        return results

    def shot_vec_statistics(self, circuit: QuantumScript):
        """Process measurement results from circuit execution using a device
        with a shot vector and return statistics.

        This is an auxiliary method of execute_new and uses statistics_new.

        When using shot vectors, measurement results for each item of the shot
        vector are contained in a tuple.

        Args:
            circuit (~.tapes.QuantumTape): circuit to execute on the device

        Raises:
            QuantumFunctionError: if the value of :attr:`~.Observable.return_type` is not supported

        Returns:
            tuple: stastics for each shot item from the shot vector
        """
        results = []
        s1 = 0

        measurements = circuit.measurements
        counts_exist = any(isinstance(m, CountsMP) for m in measurements)
        single_measurement = len(measurements) == 1

        for shot_tuple in self._shot_vector:
            s2 = s1 + np.prod(shot_tuple)
            r = self._statistics_new(circuit, shot_range=[s1, s2], bin_size=shot_tuple.shots)

            # This will likely be required:
            # if qml.math.get_interface(*r) == "jax":  # pylint: disable=protected-access
            #     r = r[0]

            if single_measurement:
                r = r[0]
            elif shot_tuple.copies == 1:
                r = tuple(r_[0] if isinstance(r_, list) else r_.T for r_ in r)
            elif counts_exist:
                r = self._multi_meas_with_counts_shot_vec(circuit, shot_tuple, r)
            else:
                # r is a nested sequence, contains the results for
                # multiple measurements
                #
                # Each item of r has copies length, we need to extract
                # each measurement result from the arrays

                # 1. transpose: applied because measurements like probs
                # for multiple copies output results with shape (N,
                # copies) and we'd like to index straight to get rows
                # which requires a shape of (copies, N)
                # 2. asarray: done because indexing into a flat array produces a
                # scalar instead of a scalar shaped array
                r = [
                    tuple(self._asarray(r_.T[idx]) for r_ in r) for idx in range(shot_tuple.copies)
                ]

            if isinstance(r, qml.numpy.ndarray):
                if shot_tuple.copies > 1:
                    results.extend([self._asarray(r_) for r_ in qml.math.unstack(r.T)])
                else:
                    results.append(r.T)

            elif single_measurement and counts_exist:
                # Results are nested in a sequence
                results.extend(r)
            elif not single_measurement and shot_tuple.copies > 1:
                # Some samples may still be transposed, fix their shapes
                # Leave dictionaries intact
                r = [tuple(elem if isinstance(elem, dict) else elem.T for elem in r_) for r_ in r]

                results.extend(r)
            else:
                results.append(r)

            s1 = s2

        return tuple(results)

    def _multi_meas_with_counts_shot_vec(self, circuit: QuantumScript, shot_tuple, r):
        """Auxiliary function of the shot_vec_statistics and execute_new
        functions for post-processing the results of multiple measurements at
        least one of which was a counts measurement.

        The measurements were executed on a device that defines a shot vector.
        """
        # First: iterate over each group of measurement
        # results that contain copies many outcomes for a
        # single measurement
        new_r = []

        # Each item of r has copies length
        for idx in range(shot_tuple.copies):
            result_group = []

            for idx2, r_ in enumerate(r):
                measurement_proc = circuit.measurements[idx2]
                if isinstance(measurement_proc, ProbabilityMP) or (
                    isinstance(measurement_proc, SampleMP) and measurement_proc.obs
                ):
                    # Here, the result has a shape of (num_basis_states, shot_tuple.copies)
                    # Extract a single row -> shape (num_basis_states,)
                    result = r_[:, idx]
                else:
                    result = r_[idx]

                if not isinstance(measurement_proc, CountsMP):
                    result = self._asarray(result.T)

                result_group.append(result)

            new_r.append(tuple(result_group))

        return new_r

    def _batch_execute_new(self, circuits):
        """Temporary batch execute function, waiting for QNode execution of the new return types. Execute a batch of
        quantum circuits on the device.

        The circuits are represented by tapes, and they are executed one-by-one using the
        device's ``execute`` method. The results are collected in a list.

        For plugin developers: This function should be overwritten if the device can efficiently run multiple
        circuits on a backend, for example using parallel and/or asynchronous executions.

        Args:
            circuits (list[.tapes.QuantumTape]): circuits to execute on the device

        Returns:
            list[array[float]]: list of measured value(s)
        """
        # TODO: This method and the tests can be globally implemented by Device
        # once it has the same signature in the execute() method

        results = []
        for circuit in circuits:
            # we need to reset the device here, else it will
            # not start the next computation in the zero state
            self.reset()

            res = self._execute_new(circuit)
            results.append(res)

        if self.tracker.active:
            self.tracker.update(batches=1, batch_len=len(circuits))
            self.tracker.record()

        return results

    def batch_execute(self, circuits):
        """Execute a batch of quantum circuits on the device.

        The circuits are represented by tapes, and they are executed one-by-one using the
        device's ``execute`` method. The results are collected in a list.

        For plugin developers: This function should be overwritten if the device can efficiently run multiple
        circuits on a backend, for example using parallel and/or asynchronous executions.

        Args:
            circuits (list[.tapes.QuantumTape]): circuits to execute on the device

        Returns:
            list[array[float]]: list of measured value(s)
        """
        # TODO: This method and the tests can be globally implemented by Device
        # once it has the same signature in the execute() method
        if qml.active_return():
            return self._batch_execute_new(circuits=circuits)

        results = []
        for circuit in circuits:
            # we need to reset the device here, else it will
            # not start the next computation in the zero state
            self.reset()

            # TODO: Insert control on value here
            res = self.execute(circuit)
            results.append(res)

        if self.tracker.active:
            self.tracker.update(batches=1, batch_len=len(circuits))
            self.tracker.record()

        return results

    @abc.abstractmethod
    def apply(self, operations, **kwargs):
        """Apply quantum operations, rotate the circuit into the measurement
        basis, and compile and execute the quantum circuit.

        This method receives a list of quantum operations queued by the QNode,
        and should be responsible for:

        * Constructing the quantum program
        * (Optional) Rotating the quantum circuit using the rotation
          operations provided. This diagonalizes the circuit so that arbitrary
          observables can be measured in the computational basis.
        * Compile the circuit
        * Execute the quantum circuit

        Both arguments are provided as lists of PennyLane :class:`~.Operation`
        instances. Useful properties include :attr:`~.Operation.name`,
        :attr:`~.Operation.wires`, and :attr:`~.Operation.parameters`,
        and :attr:`~.Operation.inverse`:

        >>> op = qml.RX(0.2, wires=[0])
        >>> op.name # returns the operation name
        "RX"
        >>> op.wires # returns a Wires object representing the wires that the operation acts on
        <Wires = [0]>
        >>> op.parameters # returns a list of parameters
        [0.2]
        >>> op.inverse # check if the operation should be inverted
        False
        >>> op = qml.RX(0.2, wires=[0]).inv
        >>> op.inverse
        True

        Args:
            operations (list[~.Operation]): operations to apply to the device

        Keyword args:
            rotations (list[~.Operation]): operations that rotate the circuit
                pre-measurement into the eigenbasis of the observables.
            hash (int): the hash value of the circuit constructed by `CircuitGraph.hash`
        """

    @staticmethod
    def active_wires(operators):
        """Returns the wires acted on by a set of operators.

        Args:
            operators (list[~.Operation]): operators for which
                we are gathering the active wires

        Returns:
            Wires: wires activated by the specified operators
        """
        list_of_wires = [op.wires for op in operators]

        return Wires.all_wires(list_of_wires)

    # pylint: disable=too-many-statements
    def statistics(
        self, observables=None, shot_range=None, bin_size=None, circuit: QuantumScript = None
    ):
        """Process measurement results from circuit execution and return statistics.

        This includes returning expectation values, variance, samples, probabilities, states, and
        density matrices.

        Args:
            circuit (~.tape.QuantumScript): the quantum script currently being executed
            shot_range (tuple[int]): 2-tuple of integers specifying the range of samples
                to use. If not specified, all samples are used.
            bin_size (int): Divides the shot range into bins of size ``bin_size``, and
                returns the measurement statistic separately over each bin. If not
                provided, the entire shot range is treated as a single bin.

        Raises:
            QuantumFunctionError: if the value of :attr:`~.Observable.return_type` is not supported

        Returns:
            Union[float, List[float]]: the corresponding statistics

        .. details::
            :title: Usage Details

            The ``shot_range`` and ``bin_size`` arguments allow for the statistics
            to be performed on only a subset of device samples. This finer level
            of control is accessible from the main UI by instantiating a device
            with a batch of shots.

            For example, consider the following device:

            >>> dev = qml.device("my_device", shots=[5, (10, 3), 100])

            This device will execute QNodes using 135 shots, however
            measurement statistics will be **coarse grained** across these 135
            shots:

            * All measurement statistics will first be computed using the
              first 5 shots --- that is, ``shots_range=[0, 5]``, ``bin_size=5``.

            * Next, the tuple ``(10, 3)`` indicates 10 shots, repeated 3 times. We will want to use
              ``shot_range=[5, 35]``, performing the expectation value in bins of size 10
              (``bin_size=10``).

            * Finally, we repeat the measurement statistics for the final 100 shots,
              ``shot_range=[35, 135]``, ``bin_size=100``.
        """
        if observables is not None:
            if isinstance(observables, QuantumScript):
                circuit = observables
                measurements = circuit.measurements
            else:
                warnings.warn(
                    message="Using a list of observables in ``QubitDevice.statistics`` is "
                    "deprecated. Please use a ``QuantumScript`` instead.",
                    category=UserWarning,
                )
                measurements = observables
        elif circuit is not None:
            measurements = circuit.measurements
        else:
            raise ValueError("Please provide a circuit into the statistics method.")

        results = []

        for m in measurements:
            # TODO: Remove this when all overriden measurements support the `MeasurementProcess` class
            if isinstance(m, MeasurementProcess) and m.obs is not None:
                obs = m.obs
                obs.return_type = m.return_type
            else:
                obs = m
            # TODO: Remove return_type when `observables` argument is removed from this method
            # Pass instances directly
            if obs.return_type is Expectation:
                # Appends a result of shape (num_bins,) if bin_size is not None, else a scalar
                results.append(self.expval(obs, shot_range=shot_range, bin_size=bin_size))

            elif obs.return_type is Variance:
                # Appends a result of shape (num_bins,) if bin_size is not None, else a scalar
                results.append(self.var(obs, shot_range=shot_range, bin_size=bin_size))

            elif obs.return_type is Sample:
                # Appends a result of shape (shots, num_bins,) if bin_size is not None else (shots,)
                results.append(
                    self.sample(obs, shot_range=shot_range, bin_size=bin_size, counts=False)
                )

            elif obs.return_type in (Counts, AllCounts):
                results.append(
                    self.sample(obs, shot_range=shot_range, bin_size=bin_size, counts=True)
                )

            elif obs.return_type is Probability:
                # Appends a result of shape (2**len(obs.wires), num_bins,)
                # if bin_size is not None else (2**len(obs.wires),)
                results.append(
                    self.probability(wires=obs.wires, shot_range=shot_range, bin_size=bin_size)
                )

            elif obs.return_type is State:
                if len(measurements) > 1:
                    raise qml.QuantumFunctionError(
                        "The state or density matrix cannot be returned in combination"
                        " with other return types"
                    )

                if self.shots is not None:
                    warnings.warn(
                        "Requested state or density matrix with finite shots; the returned "
                        "state information is analytic and is unaffected by sampling. To silence "
                        "this warning, set shots=None on the device.",
                        UserWarning,
                    )

                # Check if the state is accessible and decide to return the state or the density
                # matrix.
                results.append(self.access_state(wires=obs.wires))

            elif obs.return_type is VnEntropy:
                if self.wires.labels != tuple(range(self.num_wires)):
                    raise qml.QuantumFunctionError(
                        "Returning the Von Neumann entropy is not supported when using custom wire labels"
                    )

                if self._shot_vector is not None:
                    raise NotImplementedError(
                        "Returning the Von Neumann entropy is not supported with shot vectors."
                    )

                if self.shots is not None:
                    warnings.warn(
                        "Requested Von Neumann entropy with finite shots; the returned "
                        "result is analytic and is unaffected by sampling. To silence "
                        "this warning, set shots=None on the device.",
                        UserWarning,
                    )

                results.append(self.vn_entropy(wires=obs.wires, log_base=obs.log_base))

            elif obs.return_type is MutualInfo:
                if self.wires.labels != tuple(range(self.num_wires)):
                    raise qml.QuantumFunctionError(
                        "Returning the mutual information is not supported when using custom wire labels"
                    )

                if self._shot_vector is not None:
                    raise NotImplementedError(
                        "Returning the mutual information is not supported with shot vectors."
                    )

                if self.shots is not None:
                    warnings.warn(
                        "Requested mutual information with finite shots; the returned "
                        "state information is analytic and is unaffected by sampling. To silence "
                        "this warning, set shots=None on the device.",
                        UserWarning,
                    )

                wires0, wires1 = obs.raw_wires
                results.append(
                    self.mutual_info(wires0=wires0, wires1=wires1, log_base=obs.log_base)
                )

            elif obs.return_type is Shadow:
                if len(measurements) > 1:
                    raise qml.QuantumFunctionError(
                        "Classical shadows cannot be returned in combination"
                        " with other return types"
                    )
                results.append(self.classical_shadow(obs, circuit))

            elif obs.return_type is ShadowExpval:
                if len(measurements) > 1:
                    raise qml.QuantumFunctionError(
                        "Classical shadows cannot be returned in combination"
                        " with other return types"
                    )
                results.append(self.shadow_expval(obs, circuit=circuit))

            elif isinstance(m, MeasurementTransform):
                if method := getattr(self, m.method_name, False):
                    results.append(method(qscript=circuit))
                else:
                    results.append(m.process(qscript=circuit, device=self))

            elif isinstance(m, (SampleMeasurement, StateMeasurement)):
                results.append(self._measure(m, shot_range=shot_range, bin_size=bin_size))

            elif obs.return_type is not None:
                raise qml.QuantumFunctionError(
                    f"Unsupported return type specified for observable {obs.name}"
                )

        return results

    def _measure(
        self,
        measurement: Union[SampleMeasurement, StateMeasurement],
        shot_range=None,
        bin_size=None,
    ):
        """Compute the corresponding measurement process depending on ``shots`` and the measurement
        type.

        Args:
            measurement (Union[SampleMeasurement, StateMeasurement]): measurement process
            shot_range (tuple[int]): 2-tuple of integers specifying the range of samples
                to use. If not specified, all samples are used.
            bin_size (int): Divides the shot range into bins of size ``bin_size``, and
                returns the measurement statistic separately over each bin. If not
                provided, the entire shot range is treated as a single bin.

        Raises:
            ValueError: if the measurement cannot be computed

        Returns:
            Union[float, dict, list[float]]: result of the measurement
        """
        if method := getattr(self, measurement.method_name, False):
            return method(measurement, shot_range=shot_range, bin_size=bin_size)
        if self.shots is None:
            if isinstance(measurement, StateMeasurement):
                return measurement.process_state(state=self.state, wire_order=self.wires)

            raise ValueError(
                "Shots must be specified in the device to compute the measurement "
                f"{measurement.__class__.__name__}"
            )
        if isinstance(measurement, StateMeasurement):
            warnings.warn(
                f"Requested measurement {measurement.__class__.__name__} with finite shots; the "
                "returned state information is analytic and is unaffected by sampling. "
                "To silence this warning, set shots=None on the device.",
                UserWarning,
            )
            return measurement.process_state(state=self.state, wire_order=self.wires)
        return measurement.process_samples(
            samples=self._samples, wire_order=self.wires, shot_range=shot_range, bin_size=bin_size
        )

    def _statistics_new(self, circuit: QuantumScript, shot_range=None, bin_size=None):
        """Process measurement results from circuit execution and return statistics.

        This includes returning expectation values, variance, samples, probabilities, states, and
        density matrices.

        Args:
            circuit (~.tape.QuantumScript): the quantum script currently being executed
            shot_range (tuple[int]): 2-tuple of integers specifying the range of samples
                to use. If not specified, all samples are used.
            bin_size (int): Divides the shot range into bins of size ``bin_size``, and
                returns the measurement statistic separately over each bin. If not
                provided, the entire shot range is treated as a single bin.

        Raises:
            QuantumFunctionError: if the value of :attr:`~.Observable.return_type` is not supported

        Returns:
            Union[float, List[float]]: the corresponding statistics

        .. details::
            :title: Usage Details

            The ``shot_range`` and ``bin_size`` arguments allow for the statistics
            to be performed on only a subset of device samples. This finer level
            of control is accessible from the main UI by instantiating a device
            with a batch of shots.

            For example, consider the following device:

            >>> dev = qml.device("my_device", shots=[5, (10, 3), 100])

            This device will execute QNodes using 135 shots, however
            measurement statistics will be **course grained** across these 135
            shots:

            * All measurement statistics will first be computed using the
              first 5 shots --- that is, ``shots_range=[0, 5]``, ``bin_size=5``.

            * Next, the tuple ``(10, 3)`` indicates 10 shots, repeated 3 times. We will want to use
              ``shot_range=[5, 35]``, performing the expectation value in bins of size 10
              (``bin_size=10``).

            * Finally, we repeat the measurement statistics for the final 100 shots,
              ``shot_range=[35, 135]``, ``bin_size=100``.
        """
        measurements = circuit.measurements
        results = []

        for m in measurements:
            # TODO: Remove this when all overriden measurements support the `MeasurementProcess` class
            if m.obs is not None:
                obs = m.obs
                obs.return_type = m.return_type
            else:
                obs = m
            # 1. Based on the measurement type, compute statistics
            # Pass instances directly
            if isinstance(m, ExpectationMP):
                result = self.expval(obs, shot_range=shot_range, bin_size=bin_size)

            elif isinstance(m, VarianceMP):
                result = self.var(obs, shot_range=shot_range, bin_size=bin_size)

            elif isinstance(m, SampleMP):
                samples = self.sample(obs, shot_range=shot_range, bin_size=bin_size, counts=False)
                result = self._asarray(qml.math.squeeze(samples))

            elif isinstance(m, CountsMP):
                result = self.sample(obs, shot_range=shot_range, bin_size=bin_size, counts=True)

            elif isinstance(m, ProbabilityMP):
                result = self.probability(wires=obs.wires, shot_range=shot_range, bin_size=bin_size)

            elif isinstance(m, StateMP):
                if len(measurements) > 1:
                    raise qml.QuantumFunctionError(
                        "The state or density matrix cannot be returned in combination"
                        " with other return types"
                    )

                if self.shots is not None:
                    warnings.warn(
                        "Requested state or density matrix with finite shots; the returned "
                        "state information is analytic and is unaffected by sampling. To silence "
                        "this warning, set shots=None on the device.",
                        UserWarning,
                    )

                # Check if the state is accessible and decide to return the state or the density
                # matrix.
                state = self.access_state(wires=obs.wires)
                result = self._asarray(state, dtype=self.C_DTYPE)

            elif isinstance(m, VnEntropyMP):
                if self.wires.labels != tuple(range(self.num_wires)):
                    raise qml.QuantumFunctionError(
                        "Returning the Von Neumann entropy is not supported when using custom wire labels"
                    )

                # TODO: qml.execute shot vec support required with new return types
                # if self._shot_vector is not None:
                #     raise NotImplementedError(
                #         "Returning the Von Neumann entropy is not supported with shot vectors."
                #     )

                if self.shots is not None:
                    warnings.warn(
                        "Requested Von Neumann entropy with finite shots; the returned "
                        "result is analytic and is unaffected by sampling. To silence "
                        "this warning, set shots=None on the device.",
                        UserWarning,
                    )
                result = self.vn_entropy(wires=obs.wires, log_base=obs.log_base)

            elif isinstance(m, MutualInfoMP):
                if self.wires.labels != tuple(range(self.num_wires)):
                    raise qml.QuantumFunctionError(
                        "Returning the mutual information is not supported when using custom wire labels"
                    )

                # TODO: qml.execute shot vec support required with new return types
                # if self._shot_vector is not None:
                #     raise NotImplementedError(
                #         "Returning the mutual information is not supported with shot vectors."
                #     )

                if self.shots is not None:
                    warnings.warn(
                        "Requested mutual information with finite shots; the returned "
                        "state information is analytic and is unaffected by sampling. To silence "
                        "this warning, set shots=None on the device.",
                        UserWarning,
                    )
                wires0, wires1 = obs.raw_wires
                result = self.mutual_info(wires0=wires0, wires1=wires1, log_base=obs.log_base)

            elif isinstance(m, ClassicalShadowMP):
                if len(measurements) > 1:
                    raise qml.QuantumFunctionError(
                        "Classical shadows cannot be returned in combination"
                        " with other return types"
                    )
                result = self.classical_shadow(obs, circuit)

            elif isinstance(m, ShadowExpvalMP):
                if len(measurements) > 1:
                    raise qml.QuantumFunctionError(
                        "Classical shadows cannot be returned in combination"
                        " with other return types"
                    )
                result = self.shadow_expval(obs, circuit=circuit)

            elif isinstance(m, MeasurementTransform):
                if method := getattr(self, m.method_name, False):
                    result = method(qscript=circuit)
                else:
                    result = m.process(qscript=circuit, device=self)

            elif isinstance(m, (SampleMeasurement, StateMeasurement)):
                if method := getattr(self, m.method_name, False):
                    result = method(obs, shot_range=shot_range, bin_size=bin_size)
                else:
                    result = self._measure(m, shot_range=shot_range, bin_size=bin_size)

            elif obs.return_type is not None:
                raise qml.QuantumFunctionError(
                    f"Unsupported return type specified for observable {obs.name}"
                )

            # 2. Post-process statistics results (if need be)
            if isinstance(
                m,
                (
                    ExpectationMP,
                    VarianceMP,
                    ProbabilityMP,
                    VnEntropyMP,
                    MutualInfoMP,
                    ShadowExpvalMP,
                ),
            ):
                # Result is a float
                result = self._asarray(result, dtype=self.R_DTYPE)

            if self._shot_vector is not None and isinstance(result, np.ndarray):
                # In the shot vector case, measurement results may be of shape (N, 1) instead of (N,)
                # Squeeze the result to transform the results
                #
                # E.g.,
                # before:
                # [[0.489]
                #  [0.511]
                #  [0.   ]
                #  [0.   ]]
                #
                # after: [0.489 0.511 0.    0.   ]
                result = qml.math.squeeze(result)

            # 3. Append to final list
            results.append(result)

        return results

    def access_state(self, wires=None):
        """Check that the device has access to an internal state and return it if available.

        Args:
            wires (Wires): wires of the reduced system

        Raises:
            QuantumFunctionError: if the device is not capable of returning the state

        Returns:
            array or tensor: the state or the density matrix of the device
        """
        if not self.capabilities().get("returns_state"):
            raise qml.QuantumFunctionError(
                "The current device is not capable of returning the state"
            )

        state = getattr(self, "state", None)

        if state is None:
            raise qml.QuantumFunctionError("The state is not available in the current device")

        if wires:
            density_matrix = self.density_matrix(wires)
            return density_matrix

        return state

    def generate_samples(self):
        r"""Returns the computational basis samples generated for all wires.

        Note that PennyLane uses the convention :math:`|q_0,q_1,\dots,q_{N-1}\rangle` where
        :math:`q_0` is the most significant bit.

        .. warning::

            This method should be overwritten on devices that
            generate their own computational basis samples, with the resulting
            computational basis samples stored as ``self._samples``.

        Returns:
             array[complex]: array of samples in the shape ``(dev.shots, dev.num_wires)``
        """
        number_of_states = 2**self.num_wires

        rotated_prob = self.analytic_probability()

        samples = self.sample_basis_states(number_of_states, rotated_prob)
        return self.states_to_binary(samples, self.num_wires)

    def sample_basis_states(self, number_of_states, state_probability):
        """Sample from the computational basis states based on the state
        probability.

        This is an auxiliary method to the generate_samples method.

        Args:
            number_of_states (int): the number of basis states to sample from
            state_probability (array[float]): the computational basis probability vector

        Returns:
            array[int]: the sampled basis states
        """
        if self.shots is None:
            raise qml.QuantumFunctionError(
                "The number of shots has to be explicitly set on the device "
                "when using sample-based measurements."
            )

        shots = self.shots

        basis_states = np.arange(number_of_states)
        if self._ndim(state_probability) == 2:
            # np.random.choice does not support broadcasting as needed here.
            return np.array(
                [np.random.choice(basis_states, shots, p=prob) for prob in state_probability]
            )

        return np.random.choice(basis_states, shots, p=state_probability)

    @staticmethod
    def generate_basis_states(num_wires, dtype=np.uint32):
        """
        Generates basis states in binary representation according to the number
        of wires specified.

        The states_to_binary method creates basis states faster (for larger
        systems at times over x25 times faster) than the approach using
        ``itertools.product``, at the expense of using slightly more memory.

        Due to the large size of the integer arrays for more than 32 bits,
        memory allocation errors may arise in the states_to_binary method.
        Hence we constraint the dtype of the array to represent unsigned
        integers on 32 bits. Due to this constraint, an overflow occurs for 32
        or more wires, therefore this approach is used only for fewer wires.

        For smaller number of wires speed is comparable to the next approach
        (using ``itertools.product``), hence we resort to that one for testing
        purposes.

        Args:
            num_wires (int): the number wires
            dtype=np.uint32 (type): the data type of the arrays to use

        Returns:
            array[int]: the sampled basis states
        """
        if 2 < num_wires < 32:
            states_base_ten = np.arange(2**num_wires, dtype=dtype)
            return QubitDevice.states_to_binary(states_base_ten, num_wires, dtype=dtype)

        # A slower, but less memory intensive method
        basis_states_generator = itertools.product((0, 1), repeat=num_wires)
        return np.fromiter(itertools.chain(*basis_states_generator), dtype=int).reshape(
            -1, num_wires
        )

    @staticmethod
    def states_to_binary(samples, num_wires, dtype=np.int64):
        """Convert basis states from base 10 to binary representation.

        This is an auxiliary method to the generate_samples method.

        Args:
            samples (array[int]): samples of basis states in base 10 representation
            num_wires (int): the number of qubits
            dtype (type): Type of the internal integer array to be used. Can be
                important to specify for large systems for memory allocation
                purposes.

        Returns:
            array[int]: basis states in binary representation
        """
        powers_of_two = 1 << np.arange(num_wires, dtype=dtype)
        # `samples` typically is one-dimensional, but can be two-dimensional with broadcasting.
        # In any case we want to append a new axis at the *end* of the shape.
        states_sampled_base_ten = samples[..., None] & powers_of_two
        # `states_sampled_base_ten` can be two- or three-dimensional. We revert the *last* axis.
        return (states_sampled_base_ten > 0).astype(dtype)[..., ::-1]

    @property
    def circuit_hash(self):
        """The hash of the circuit upon the last execution.

        This can be used by devices in :meth:`~.apply` for parametric compilation.
        """
        raise NotImplementedError

    @property
    def state(self):
        """Returns the state vector of the circuit prior to measurement.

        .. note::

            Only state vector simulators support this property. Please see the
            plugin documentation for more details.
        """
        raise NotImplementedError

    def density_matrix(self, wires):
        """Returns the reduced density matrix over the given wires.

        Args:
            wires (Wires): wires of the reduced system

        Returns:
            array[complex]: complex array of shape ``(2 ** len(wires), 2 ** len(wires))``
            representing the reduced density matrix of the state prior to measurement.
        """
        state = getattr(self, "state", None)
        wires = self.map_wires(wires)
        return qml.math.reduced_dm(state, indices=wires, c_dtype=self.C_DTYPE)

    def vn_entropy(self, wires, log_base):
        r"""Returns the Von Neumann entropy prior to measurement.

        .. math::
            S( \rho ) = -\text{Tr}( \rho \log ( \rho ))

        Args:
            wires (Wires): Wires of the considered subsystem.
            log_base (float): Base for the logarithm, default is None the natural logarithm is used in this case.

        Returns:
            float: returns the Von Neumann entropy
        """
        try:
            state = self.access_state()
        except qml.QuantumFunctionError as e:  # pragma: no cover
            raise NotImplementedError(
                f"Cannot compute the Von Neumman entropy with device {self.name} that is not capable of returning the "
                f"state. "
            ) from e
        wires = wires.tolist()
        return qml.math.vn_entropy(state, indices=wires, c_dtype=self.C_DTYPE, base=log_base)

    def mutual_info(self, wires0, wires1, log_base):
        r"""Returns the mutual information prior to measurement:

        .. math::

            I(A, B) = S(\rho^A) + S(\rho^B) - S(\rho^{AB})

        where :math:`S` is the von Neumann entropy.

        Args:
            wires0 (Wires): wires of the first subsystem
            wires1 (Wires): wires of the second subsystem
            log_base (float): base to use in the logarithm

        Returns:
            float: the mutual information
        """
        try:
            state = self.access_state()
        except qml.QuantumFunctionError as e:  # pragma: no cover
            raise NotImplementedError(
                f"Cannot compute the mutual information with device {self.name} that is not capable of returning the "
                f"state. "
            ) from e

        wires0 = wires0.tolist()
        wires1 = wires1.tolist()

        return qml.math.mutual_info(
            state, indices0=wires0, indices1=wires1, c_dtype=self.C_DTYPE, base=log_base
        )

    def classical_shadow(self, obs, circuit):
        """
        Returns the measured bits and recipes in the classical shadow protocol.

        The protocol is described in detail in the `classical shadows paper <https://arxiv.org/abs/2002.08953>`_.
        This measurement process returns the randomized Pauli measurements (the ``recipes``)
        that are performed for each qubit and snapshot as an integer:

        - 0 for Pauli X,
        - 1 for Pauli Y, and
        - 2 for Pauli Z.

        It also returns the measurement results (the ``bits``); 0 if the 1 eigenvalue
        is sampled, and 1 if the -1 eigenvalue is sampled.

        The device shots are used to specify the number of snapshots. If ``T`` is the number
        of shots and ``n`` is the number of qubits, then both the measured bits and the
        Pauli measurements have shape ``(T, n)``.

        This implementation is device-agnostic and works by executing single-shot
        tapes containing randomized Pauli observables. Devices should override this
        if they can offer cleaner or faster implementations.

        .. seealso:: :func:`~.classical_shadow`

        Args:
            obs (~.pennylane.measurements.ClassicalShadow): The classical shadow measurement process
            circuit (~.tapes.QuantumTape): The quantum tape that is being executed

        Returns:
            tensor_like[int]: A tensor with shape ``(2, T, n)``, where the first row represents
            the measured bits and the second represents the recipes used.
        """
        if circuit is None:  # pragma: no cover
            raise ValueError("Circuit must be provided when measuring classical shadows")

        wires = obs.wires
        n_snapshots = self.shots
        seed = obs.seed

        with set_shots(self, shots=1):
            # slow implementation but works for all devices
            n_qubits = len(wires)
            mapped_wires = np.array(self.map_wires(wires))

            # seed the random measurement generation so that recipes
            # are the same for different executions with the same seed
            rng = np.random.RandomState(seed)
            recipes = rng.randint(0, 3, size=(n_snapshots, n_qubits))
            obs_list = [qml.PauliX, qml.PauliY, qml.PauliZ]

            outcomes = np.zeros((n_snapshots, n_qubits))

            for t in range(n_snapshots):
                # compute rotations for the Pauli measurements
                rotations = [
                    rot
                    for wire_idx, wire in enumerate(wires)
                    for rot in obs_list[recipes[t][wire_idx]].compute_diagonalizing_gates(
                        wires=wire
                    )
                ]

                self.reset()
                self.apply(circuit.operations, rotations=circuit.diagonalizing_gates + rotations)

                outcomes[t] = self.generate_samples()[0][mapped_wires]

        return self._cast(self._stack([outcomes, recipes]), dtype=np.int8)

    def shadow_expval(self, obs, circuit):
        r"""Compute expectation values using classical shadows in a differentiable manner.

        Please refer to :func:`~.pennylane.shadow_expval` for detailed documentation.

        Args:
            obs (~.pennylane.measurements.ClassicalShadow): The classical shadow expectation
                value measurement process
            circuit (~.tapes.QuantumTape): The quantum tape that is being executed

        Returns:
            float: expectation value estimate.
        """
        bits, recipes = self.classical_shadow(obs, circuit)
        shadow = qml.shadows.ClassicalShadow(bits, recipes, wire_map=obs.wires.tolist())
        return shadow.expval(obs.H, obs.k)

    def analytic_probability(self, wires=None):
        r"""Return the (marginal) probability of each computational basis
        state from the last run of the device.

        PennyLane uses the convention
        :math:`|q_0,q_1,\dots,q_{N-1}\rangle` where :math:`q_0` is the most
        significant bit.

        If no wires are specified, then all the basis states representable by
        the device are considered and no marginalization takes place.


        .. note::

            :meth:`marginal_prob` may be used as a utility method
            to calculate the marginal probability distribution.

        Args:
            wires (Iterable[Number, str], Number, str, Wires): wires to return
                marginal probabilities for. Wires not provided are traced out of the system.

        Returns:
            array[float]: list of the probabilities
        """
        raise NotImplementedError

    def estimate_probability(self, wires=None, shot_range=None, bin_size=None):
        """Return the estimated probability of each computational basis state
        using the generated samples.

        Args:
            wires (Iterable[Number, str], Number, str, Wires): wires to calculate
                marginal probabilities for. Wires not provided are traced out of the system.
            shot_range (tuple[int]): 2-tuple of integers specifying the range of samples
                to use. If not specified, all samples are used.
            bin_size (int): Divides the shot range into bins of size ``bin_size``, and
                returns the measurement statistic separately over each bin. If not
                provided, the entire shot range is treated as a single bin.

        Returns:
            array[float]: list of the probabilities
        """

        wires = wires or self.wires
        # convert to a Wires object
        wires = Wires(wires)
        # translate to wire labels used by device
        device_wires = self.map_wires(wires)
        num_wires = len(device_wires)

        if shot_range is None:
            # The Ellipsis (...) corresponds to broadcasting and shots dimensions or only shots
            samples = self._samples[..., device_wires]
        else:
            # The Ellipsis (...) corresponds to the broadcasting dimension or no axis at all
            samples = self._samples[..., slice(*shot_range), device_wires]

        # convert samples from a list of 0, 1 integers, to base 10 representation
        powers_of_two = 2 ** np.arange(num_wires)[::-1]
        indices = samples @ powers_of_two

        # `self._samples` typically has two axes ((shots, wires)) but can also have three with
        # broadcasting ((batch_size, shots, wires)) so that we simply read out the batch_size.
        batch_size = self._samples.shape[0] if np.ndim(self._samples) == 3 else None
        dim = 2**num_wires
        # count the basis state occurrences, and construct the probability vector
        if bin_size is not None:
            num_bins = samples.shape[-2] // bin_size
            prob = self._count_binned_samples(indices, batch_size, dim, bin_size, num_bins)
        else:
            prob = self._count_unbinned_samples(indices, batch_size, dim)

        return self._asarray(prob, dtype=self.R_DTYPE)

    @staticmethod
    def _count_unbinned_samples(indices, batch_size, dim):
        """Count the occurences of sampled indices and convert them to relative
        counts in order to estimate their occurence probability."""
        if batch_size is None:
            prob = np.zeros(dim, dtype=np.float64)
            basis_states, counts = np.unique(indices, return_counts=True)
            prob[basis_states] = counts / len(indices)

            return prob

        prob = np.zeros((batch_size, dim), dtype=np.float64)

        for i, idx in enumerate(indices):  # iterate over the broadcasting dimension
            basis_states, counts = np.unique(idx, return_counts=True)
            prob[i, basis_states] = counts / len(idx)

        return prob

    @staticmethod
    def _count_binned_samples(indices, batch_size, dim, bin_size, num_bins):
        """Count the occurences of bins of sampled indices and convert them to relative
        counts in order to estimate their occurence probability per bin."""

        if batch_size is None:
            prob = np.zeros((dim, num_bins), dtype=np.float64)
            indices = indices.reshape((num_bins, bin_size))
            # count the basis state occurrences, and construct the probability vector for each bin
            for b, idx in enumerate(indices):
                basis_states, counts = np.unique(idx, return_counts=True)
                prob[basis_states, b] = counts / bin_size

            return prob

        prob = np.zeros((batch_size, dim, num_bins), dtype=np.float64)
        indices = indices.reshape((batch_size, num_bins, bin_size))

        # count the basis state occurrences, and construct the probability vector
        # for each bin and broadcasting index
        for i, _indices in enumerate(indices):  # First iterate over broadcasting dimension
            for b, idx in enumerate(_indices):  # Then iterate over bins dimension
                basis_states, counts = np.unique(idx, return_counts=True)
                prob[i, basis_states, b] = counts / bin_size

        return prob

    def probability(self, wires=None, shot_range=None, bin_size=None):
        """Return either the analytic probability or estimated probability of
        each computational basis state.

        Devices that require a finite number of shots always return the
        estimated probability.

        Args:
            wires (Iterable[Number, str], Number, str, Wires): wires to return
                marginal probabilities for. Wires not provided are traced out of the system.

        Returns:
            array[float]: list of the probabilities
        """
        wires = wires or self.wires
        if self.shots is None:
            return self.analytic_probability(wires=wires)

        return self.estimate_probability(wires=wires, shot_range=shot_range, bin_size=bin_size)

    @staticmethod
    def _get_batch_size(tensor, expected_shape, expected_size):
        """Determine whether a tensor has an additional batch dimension for broadcasting,
        compared to an expected_shape. As QubitDevice does not natively support broadcasting,
        it always reports no batch size, that is ``batch_size=None``"""
        # pylint: disable=unused-argument
        return None

    def marginal_prob(self, prob, wires=None):
        r"""Return the marginal probability of the computational basis
        states by summing the probabiliites on the non-specified wires.

        If no wires are specified, then all the basis states representable by
        the device are considered and no marginalization takes place.

        .. note::

            If the provided wires are not in the order as they appear on the device,
            the returned marginal probabilities take this permutation into account.

            For example, if the addressable wires on this device are ``Wires([0, 1, 2])`` and
            this function gets passed ``wires=[2, 0]``, then the returned marginal
            probability vector will take this 'reversal' of the two wires
            into account:

            .. math::

                \mathbb{P}^{(2, 0)}
                            = \left[
                               |00\rangle, |10\rangle, |01\rangle, |11\rangle
                              \right]

        Args:
            prob: The probabilities to return the marginal probabilities
                for
            wires (Iterable[Number, str], Number, str, Wires): wires to return
                marginal probabilities for. Wires not provided
                are traced out of the system.

        Returns:
            array[float]: array of the resulting marginal probabilities.
        """
        dim = 2**self.num_wires
        batch_size = self._get_batch_size(prob, (dim,), dim)  # pylint: disable=assignment-from-none

        if wires is None:
            # no need to marginalize
            return prob

        wires = Wires(wires)
        # determine which subsystems are to be summed over
        inactive_wires = Wires.unique_wires([self.wires, wires])

        # translate to wire labels used by device
        device_wires = self.map_wires(wires)
        inactive_device_wires = self.map_wires(inactive_wires)

        # reshape the probability so that each axis corresponds to a wire
        shape = [2] * self.num_wires
        if batch_size is not None:
            shape.insert(0, batch_size)
        # prob now is reshaped to have self.num_wires+1 axes in the case of broadcasting
        prob = self._reshape(prob, shape)

        # sum over all inactive wires
        # hotfix to catch when default.qubit uses this method
        # since then device_wires is a list
        if isinstance(inactive_device_wires, Wires):
            inactive_device_wires = inactive_device_wires.labels

        if batch_size is not None:
            inactive_device_wires = [idx + 1 for idx in inactive_device_wires]
        flat_shape = (-1,) if batch_size is None else (batch_size, -1)
        prob = self._reshape(self._reduce_sum(prob, inactive_device_wires), flat_shape)

        # The wires provided might not be in consecutive order (i.e., wires might be [2, 0]).
        # If this is the case, we must permute the marginalized probability so that
        # it corresponds to the orders of the wires passed.
        num_wires = len(device_wires)
        basis_states = self.generate_basis_states(num_wires)
        basis_states = basis_states[:, np.argsort(np.argsort(device_wires))]

        powers_of_two = 2 ** np.arange(len(device_wires))[::-1]
        perm = basis_states @ powers_of_two
        # The permutation happens on the last axis both with and without broadcasting
        return self._gather(prob, perm, axis=1 if batch_size is not None else 0)

    def expval(self, observable, shot_range=None, bin_size=None):

        if observable.name == "Projector":
            # branch specifically to handle the projector observable
            idx = int("".join(str(i) for i in observable.parameters[0]), 2)
            probs = self.probability(
                wires=observable.wires, shot_range=shot_range, bin_size=bin_size
            )
            return probs[idx]

        # exact expectation value
        if self.shots is None:
            try:
                eigvals = self._asarray(observable.eigvals(), dtype=self.R_DTYPE)
            except qml.operation.EigvalsUndefinedError as e:
                raise qml.operation.EigvalsUndefinedError(
                    f"Cannot compute analytic expectations of {observable.name}."
                ) from e

            # the probability vector must be permuted to account for the permuted
            # wire order of the observable
            permuted_wires = self._permute_wires(observable)

            prob = self.probability(wires=permuted_wires)
            # In case of broadcasting, `prob` has two axes and this is a matrix-vector product
            return self._dot(prob, eigvals)

        # estimate the ev
        samples = self.sample(observable, shot_range=shot_range, bin_size=bin_size)
        # With broadcasting, we want to take the mean over axis 1, which is the -1st/-2nd with/
        # without bin_size. Without broadcasting, axis 0 is the -1st/-2nd with/without bin_size
        axis = -1 if bin_size is None else -2
        # TODO: do we need to squeeze here? Maybe remove with new return types
        return np.squeeze(np.mean(samples, axis=axis))

    def var(self, observable, shot_range=None, bin_size=None):

        if observable.name == "Projector":
            # branch specifically to handle the projector observable
            idx = int("".join(str(i) for i in observable.parameters[0]), 2)
            probs = self.probability(
                wires=observable.wires, shot_range=shot_range, bin_size=bin_size
            )
            return probs[idx] - probs[idx] ** 2

        # exact variance value
        if self.shots is None:
            try:
                eigvals = self._asarray(observable.eigvals(), dtype=self.R_DTYPE)
            except qml.operation.EigvalsUndefinedError as e:
                # if observable has no info on eigenvalues, we cannot return this measurement
                raise qml.operation.EigvalsUndefinedError(
                    f"Cannot compute analytic variance of {observable.name}."
                ) from e

            # the probability vector must be permuted to account for the permuted wire order of the observable
            permuted_wires = self._permute_wires(observable)

            prob = self.probability(wires=permuted_wires)
            # In case of broadcasting, `prob` has two axes and these are a matrix-vector products
            return self._dot(prob, (eigvals**2)) - self._dot(prob, eigvals) ** 2

        # estimate the variance
        samples = self.sample(observable, shot_range=shot_range, bin_size=bin_size)
        # With broadcasting, we want to take the variance over axis 1, which is the -1st/-2nd with/
        # without bin_size. Without broadcasting, axis 0 is the -1st/-2nd with/without bin_size
        axis = -1 if bin_size is None else -2
        # TODO: do we need to squeeze here? Maybe remove with new return types
        return np.squeeze(np.var(samples, axis=axis))

    def _samples_to_counts(self, samples, obs, num_wires):
        """Groups the samples into a dictionary showing number of occurences for
        each possible outcome.

        The format of the dictionary depends on obs.return_type, which is set when
        calling measurements.counts by setting the kwarg all_outcomes (bool). By default,
        the dictionary will only contain the observed outcomes. Optionally (all_outcomes=True)
        the dictionary will instead contain all possible outcomes, with a count of 0
        for those not observed. See example.


        Args:
            samples: samples in an array of dimension ``(shots,len(wires))``
            obs (Observable): the observable sampled
            num_wires (int): number of wires the sampled observable was performed on

        Returns:
            dict: dictionary with format ``{'outcome': num_occurences}``, including all
                outcomes for the sampled observable

        **Example**

            >>> samples
            tensor([[0, 0],
                    [0, 0],
                    [1, 0]], requires_grad=True)

            By default, this will return:
            >>> self._samples_to_counts(samples, obs, num_wires)
            {'00': 2, '10': 1}

            However, if obs.return_type is AllCounts, this will return:
            >>> self._samples_to_counts(samples, obs, num_wires)
            {'00': 2, '01': 0, '10': 1, '11': 0}

            The variable all_outcomes can be set when running measurements.counts, i.e.:

             .. code-block:: python3

                dev = qml.device("default.qubit", wires=2, shots=4)

                @qml.qnode(dev)
                def circuit(x):
                    qml.RX(x, wires=0)
                    return qml.counts(all_outcomes=True)

        """

        outcomes = []

        if isinstance(obs, CountsMP):
            # convert samples and outcomes (if using) from arrays to str for dict keys
            samples = ["".join([str(s.item()) for s in sample]) for sample in samples]

            if obs.all_outcomes:
                outcomes = self.generate_basis_states(num_wires)
                outcomes = ["".join([str(o.item()) for o in outcome]) for outcome in outcomes]
        elif obs.return_type is AllCounts:
            outcomes = qml.eigvals(obs)

        # generate empty outcome dict, populate values with state counts
        outcome_dict = {k: np.int64(0) for k in outcomes}
        states, counts = np.unique(samples, return_counts=True)
        for s, c in zip(states, counts):
            outcome_dict[s] = c

        return outcome_dict

    def sample(self, observable, shot_range=None, bin_size=None, counts=False):
        """Return samples of an observable.

        Args:
            observable (Observable): the observable to sample
            shot_range (tuple[int]): 2-tuple of integers specifying the range of samples
                to use. If not specified, all samples are used.
            bin_size (int): Divides the shot range into bins of size ``bin_size``, and
                returns the measurement statistic separately over each bin. If not
                provided, the entire shot range is treated as a single bin.
            counts (bool): whether counts (``True``) or raw samples (``False``)
                should be returned

        Raises:
            EigvalsUndefinedError: if no information is available about the
                eigenvalues of the observable

        Returns:
            Union[array[float], dict, list[dict]]: samples in an array of
            dimension ``(shots,)`` or counts
        """

        # translate to wire labels used by device
        device_wires = self.map_wires(observable.wires)
        name = observable.name
        # Select the samples from self._samples that correspond to ``shot_range`` if provided
        if shot_range is None:
            sub_samples = self._samples
        else:
            # Indexing corresponds to: (potential broadcasting, shots, wires). Note that the last
            # colon (:) is required because shots is the second-to-last axis and the
            # Ellipsis (...) otherwise would take up broadcasting and shots axes.
            sub_samples = self._samples[..., slice(*shot_range), :]

        no_observable_provided = isinstance(observable, MeasurementProcess)

        if isinstance(name, str) and name in {"PauliX", "PauliY", "PauliZ", "Hadamard"}:
            # Process samples for observables with eigenvalues {1, -1}
            samples = 1 - 2 * sub_samples[..., device_wires[0]]

        elif no_observable_provided:
            # if no observable was provided then return the raw samples
            if len(observable.wires) != 0:
                # if wires are provided, then we only return samples from those wires
                samples = sub_samples[..., np.array(device_wires)]
            else:
                samples = sub_samples

        else:

            # Replace the basis state in the computational basis with the correct eigenvalue.
            # Extract only the columns of the basis samples required based on ``wires``.
            samples = sub_samples[..., np.array(device_wires)]  # Add np.array here for Jax support.
            powers_of_two = 2 ** np.arange(samples.shape[-1])[::-1]
            indices = samples @ powers_of_two
            indices = np.array(indices)  # Add np.array here for Jax support.
            try:
                samples = observable.eigvals()[indices]
            except qml.operation.EigvalsUndefinedError as e:
                # if observable has no info on eigenvalues, we cannot return this measurement
                raise qml.operation.EigvalsUndefinedError(
                    f"Cannot compute samples of {observable.name}."
                ) from e

        num_wires = len(device_wires) if len(device_wires) > 0 else self.num_wires
        if bin_size is None:
            if counts:
                return self._samples_to_counts(samples, observable, num_wires)
            return samples

        if counts:
            shape = (-1, bin_size, num_wires) if no_observable_provided else (-1, bin_size)
            return [
                self._samples_to_counts(bin_sample, observable, num_wires)
                for bin_sample in samples.reshape(shape)
            ]

        return (
            samples.T.reshape((num_wires, bin_size, -1))
            if no_observable_provided
            else samples.reshape((bin_size, -1))
        )

    def adjoint_jacobian(
        self, tape: QuantumScript, starting_state=None, use_device_state=False
    ):  # pylint: disable=too-many-statements
        """Implements the adjoint method outlined in
        `Jones and Gacon <https://arxiv.org/abs/2009.02823>`__ to differentiate an input tape.

        After a forward pass, the circuit is reversed by iteratively applying inverse (adjoint)
        gates to scan backwards through the circuit.

        .. note::
            The adjoint differentiation method has the following restrictions:

            * As it requires knowledge of the statevector, only statevector simulator devices can be
              used.

            * Only expectation values are supported as measurements.

            * Does not work for parametrized observables like
              :class:`~.Hamiltonian` or :class:`~.Hermitian`.

        Args:
            tape (.QuantumScript): circuit that the function takes the gradient of

        Keyword Args:
            starting_state (tensor_like): post-forward pass state to start execution with. It should be
                complex-valued. Takes precedence over ``use_device_state``.
            use_device_state (bool): use current device state to initialize. A forward pass of the same
                circuit should be the last thing the device has executed. If a ``starting_state`` is
                provided, that takes precedence.

        Returns:
            array or tuple[array]: the derivative of the tape with respect to trainable parameters.
            Dimensions are ``(len(observables), len(trainable_params))``.

        Raises:
            QuantumFunctionError: if the input tape has measurements that are not expectation values
                or contains a multi-parameter operation aside from :class:`~.Rot`
        """
        # broadcasted inner product not summing over first dimension of b
        sum_axes = tuple(range(1, self.num_wires + 1))
        # pylint: disable=unnecessary-lambda-assignment
        dot_product_real = lambda b, k: self._real(qmlsum(self._conj(b) * k, axis=sum_axes))

        for m in tape.measurements:
            if not isinstance(m, ExpectationMP):
                raise qml.QuantumFunctionError(
                    "Adjoint differentiation method does not support"
                    f" measurement {m.__class__.__name__}"
                )

            if m.obs.name == "Hamiltonian":
                raise qml.QuantumFunctionError(
                    "Adjoint differentiation method does not support Hamiltonian observables."
                )

            if not hasattr(m.obs, "base_name"):
                m.obs.base_name = None  # This is needed for when the observable is a tensor product

        if self.shot_vector is not None:
            raise qml.QuantumFunctionError("Adjoint does not support shot vectors.")

        if self.shots is not None:
            warnings.warn(
                "Requested adjoint differentiation to be computed with finite shots."
                " The derivative is always exact when using the adjoint differentiation method.",
                UserWarning,
            )

        # Initialization of state
        if starting_state is not None:
            ket = self._reshape(starting_state, [2] * self.num_wires)
        else:
            if not use_device_state:
                self.reset()
                self.execute(tape)
            ket = self._pre_rotated_state

        n_obs = len(tape.observables)
        bras = np.empty([n_obs] + [2] * self.num_wires, dtype=np.complex128)
        for kk in range(n_obs):
            bras[kk, ...] = self._apply_operation(ket, tape.observables[kk])

        expanded_ops = []
        for op in reversed(tape.operations):
            if op.num_params > 1:
                if not isinstance(op, qml.Rot) or op.inverse:
                    raise qml.QuantumFunctionError(
                        f"The {op.name} operation is not supported using "
                        'the "adjoint" differentiation method'
                    )
                ops = op.decomposition()
                expanded_ops.extend(reversed(ops))
            elif op.name not in ("QubitStateVector", "BasisState", "Snapshot"):
                expanded_ops.append(op)

        trainable_params = []
        for k in tape.trainable_params:
            # pylint: disable=protected-access
            if hasattr(tape._par_info[k]["op"], "return_type"):
                warnings.warn(
                    "Differentiating with respect to the input parameters of "
                    f"{tape._par_info[k]['op'].name} is not supported with the "
                    "adjoint differentiation method. Gradients are computed "
                    "only with regards to the trainable parameters of the circuit.\n\n Mark "
                    "the parameters of the measured observables as non-trainable "
                    "to silence this warning.",
                    UserWarning,
                )
            else:
                trainable_params.append(k)

        jac = np.zeros((len(tape.observables), len(trainable_params)))

        param_number = len(tape.get_parameters(trainable_only=False, operations_only=True)) - 1
        trainable_param_number = len(trainable_params) - 1
        for op in expanded_ops:

            adj_op = qml.adjoint(op)
            ket = self._apply_operation(ket, adj_op)

            if op.grad_method is not None:
                if param_number in trainable_params:
                    d_op_matrix = operation_derivative(op)
                    ket_temp = self._apply_unitary(ket, d_op_matrix, op.wires)

                    jac[:, trainable_param_number] = 2 * dot_product_real(bras, ket_temp)

                    trainable_param_number -= 1
                param_number -= 1

            for kk in range(n_obs):
                bras[kk, ...] = self._apply_operation(bras[kk, ...], adj_op)

        return self._adjoint_jacobian_processing(jac) if qml.active_return() else jac

    @staticmethod
    def _adjoint_jacobian_processing(jac):
        """
        Post-process the Jacobian matrix returned by ``adjoint_jacobian`` for
        the new return type system.
        """
        jac = np.squeeze(jac)

        if jac.ndim == 0:
            return np.array(jac)

        if jac.ndim == 1:
            return tuple(np.array(j) for j in jac)

        # must be 2-dimensional
        return tuple(tuple(np.array(j_) for j_ in j) for j in jac)
