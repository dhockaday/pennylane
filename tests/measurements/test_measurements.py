# Copyright 2018-2020 Xanadu Quantum Technologies Inc.

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Unit tests for the measurements module"""
import numpy as np
import pytest

import pennylane as qml
from pennylane.measurements import (
    ClassicalShadowMP,
    Counts,
    CountsMP,
    Expectation,
    ExpectationMP,
    MeasurementProcess,
    MeasurementTransform,
    MidMeasure,
    MutualInfoMP,
    Probability,
    ProbabilityMP,
    Sample,
    SampleMeasurement,
    SampleMP,
    ShadowExpvalMP,
    State,
    StateMeasurement,
    StateMP,
    Variance,
    VarianceMP,
    VnEntropyMP,
    expval,
    sample,
    var,
)
from pennylane.operation import DecompositionUndefinedError
from pennylane.queuing import AnnotatedQueue


class NotValidMeasurement(MeasurementProcess):
    @property
    def return_type(self):
        return "NotValidReturnType"


@pytest.mark.parametrize(
    "return_type, value",
    [
        (Expectation, "expval"),
        (Sample, "sample"),
        (Counts, "counts"),
        (Variance, "var"),
        (Probability, "probs"),
        (State, "state"),
        (MidMeasure, "measure"),
    ],
)
def test_ObservableReturnTypes(return_type, value):
    """Test the ObservableReturnTypes enum value, repr, and enum membership."""

    assert return_type.value == value
    assert isinstance(return_type, qml.measurements.ObservableReturnTypes)
    assert repr(return_type) == value


def test_no_measure():
    """Test that failing to specify a measurement
    raises an exception"""
    dev = qml.device("default.qubit", wires=2)

    @qml.qnode(dev)
    def circuit(x):
        qml.RX(x, wires=0)
        return qml.PauliY(0)

    with pytest.raises(qml.QuantumFunctionError, match="must return either a single measurement"):
        _ = circuit(0.65)


def test_numeric_type_unrecognized_error():
    """Test that querying the numeric type of a measurement process with an
    unrecognized return type raises an error."""

    mp = NotValidMeasurement()
    with pytest.raises(
        qml.QuantumFunctionError,
        match="The numeric type of the measurement NotValidMeasurement is not defined",
    ):
        mp.numeric_type()


def test_shape_unrecognized_error():
    """Test that querying the shape of a measurement process with an
    unrecognized return type raises an error."""
    dev = qml.device("default.qubit", wires=2)
    mp = NotValidMeasurement()
    with pytest.raises(
        qml.QuantumFunctionError,
        match="The shape of the measurement NotValidMeasurement is not defined",
    ):
        mp.shape(dev)


@pytest.mark.parametrize(
    "stat_func,return_type", [(expval, Expectation), (var, Variance), (sample, Sample)]
)
class TestStatisticsQueuing:
    """Tests for annotating the return types of the statistics functions"""

    @pytest.mark.parametrize(
        "op",
        [qml.PauliX, qml.PauliY, qml.PauliZ, qml.Hadamard, qml.Identity],
    )
    def test_annotating_obs_return_type(self, stat_func, return_type, op):
        """Test that the return_type related info is updated for a
        measurement"""
        with AnnotatedQueue() as q:
            A = op(0)
            stat_func(A)

        assert q.queue[:-1] == [A]
        meas_proc = q.queue[-1]
        assert isinstance(meas_proc, MeasurementProcess)
        assert meas_proc.return_type == return_type

        assert q.get_info(A) == {"owner": meas_proc}
        assert q.get_info(meas_proc) == {"owns": (A)}

    def test_annotating_tensor_hermitian(self, stat_func, return_type):
        """Test that the return_type related info is updated for a measurement
        when called for an Hermitian observable"""

        mx = np.array([[1, 0], [0, 1]])

        with AnnotatedQueue() as q:
            Herm = qml.Hermitian(mx, wires=[1])
            stat_func(Herm)

        assert q.queue[:-1] == [Herm]
        meas_proc = q.queue[-1]
        assert isinstance(meas_proc, MeasurementProcess)
        assert meas_proc.return_type == return_type

        assert q.get_info(Herm) == {"owner": meas_proc}
        assert q.get_info(meas_proc) == {"owns": (Herm)}

    @pytest.mark.parametrize(
        "op1,op2",
        [
            (qml.PauliY, qml.PauliX),
            (qml.Hadamard, qml.Hadamard),
            (qml.PauliY, qml.Identity),
            (qml.Identity, qml.Identity),
        ],
    )
    def test_annotating_tensor_return_type(self, op1, op2, stat_func, return_type):
        """Test that the return_type related info is updated for a measurement
        when called for an Tensor observable"""
        with AnnotatedQueue() as q:
            A = op1(0)
            B = op2(1)
            tensor_op = A @ B
            stat_func(tensor_op)

        assert q.queue[:-1] == [A, B, tensor_op]
        meas_proc = q.queue[-1]
        assert isinstance(meas_proc, MeasurementProcess)
        assert meas_proc.return_type == return_type

        assert q.get_info(A) == {"owner": tensor_op}
        assert q.get_info(B) == {"owner": tensor_op}
        assert q.get_info(tensor_op) == {"owns": (A, B), "owner": meas_proc}

    @pytest.mark.parametrize(
        "op1,op2",
        [
            (qml.PauliY, qml.PauliX),
            (qml.Hadamard, qml.Hadamard),
            (qml.PauliY, qml.Identity),
            (qml.Identity, qml.Identity),
        ],
    )
    def test_queueing_tensor_observable(self, op1, op2, stat_func, return_type):
        """Test that if the constituent components of a tensor operation are not
        found in the queue for annotation, they are not queued or annotated."""
        A = op1(0)
        B = op2(1)

        with AnnotatedQueue() as q:
            tensor_op = A @ B
            stat_func(tensor_op)

        assert len(q) == 2

        assert q.queue[0] is tensor_op
        meas_proc = q.queue[-1]
        assert isinstance(meas_proc, MeasurementProcess)
        assert meas_proc.return_type == return_type

        assert q.get_info(tensor_op) == {"owns": (A, B), "owner": meas_proc}

    def test_not_an_observable(self, stat_func, return_type):
        """Test that a UserWarning is raised if the provided
        argument might not be hermitian."""
        if stat_func is sample:
            pytest.skip("Sampling is not yet supported with symbolic operators.")

        dev = qml.device("default.qubit", wires=2)

        @qml.qnode(dev)
        def circuit():
            qml.RX(0.52, wires=0)
            return stat_func(qml.prod(qml.PauliX(0), qml.PauliZ(0)))

        with pytest.warns(UserWarning, match="Prod might not be hermitian."):
            _ = circuit()


class TestProperties:
    """Test for the properties"""

    def test_return_type(self):
        """Test MeasurementProcess ``return_type`` property is None."""
        assert MeasurementProcess().return_type is None

    def test_wires_match_observable(self):
        """Test that the wires of the measurement process
        match an internal observable"""
        obs = qml.Hermitian(np.diag([1, 2, 3, 4]), wires=["a", "b"])
        m = qml.expval(op=obs)

        assert np.all(m.wires == obs.wires)

    def test_eigvals_match_observable(self):
        """Test that the eigenvalues of the measurement process
        match an internal observable"""
        obs = qml.Hermitian(np.diag([1, 2, 3, 4]), wires=[0, 1])
        m = qml.expval(op=obs)

        assert np.all(m.eigvals() == np.array([1, 2, 3, 4]))

        # changing the observable data should be reflected
        obs.data = [np.diag([5, 6, 7, 8])]
        assert np.all(m.eigvals() == np.array([5, 6, 7, 8]))

    def test_error_obs_and_eigvals(self):
        """Test that providing both eigenvalues and an observable
        results in an error"""
        obs = qml.Hermitian(np.diag([1, 2, 3, 4]), wires=[0, 1])

        with pytest.raises(ValueError, match="Cannot set the eigenvalues"):
            ExpectationMP(obs=obs, eigvals=[0, 1])

    def test_error_obs_and_wires(self):
        """Test that providing both wires and an observable
        results in an error"""
        obs = qml.Hermitian(np.diag([1, 2, 3, 4]), wires=[0, 1])

        with pytest.raises(ValueError, match="Cannot set the wires"):
            ExpectationMP(obs=obs, wires=qml.wires.Wires([0, 1]))

    def test_observable_with_no_eigvals(self):
        """An observable with no eigenvalues defined should cause
        the eigvals method to return a NotImplementedError"""
        obs = qml.NumberOperator(wires=0)
        m = qml.expval(op=obs)
        assert m.eigvals() is None

    def test_repr(self):
        """Test the string representation of a MeasurementProcess."""
        m = qml.expval(op=qml.PauliZ(wires="a") @ qml.PauliZ(wires="b"))
        expected = "expval(PauliZ(wires=['a']) @ PauliZ(wires=['b']))"
        assert str(m) == expected

        m = qml.probs(op=qml.PauliZ(wires="a"))
        expected = "probs(PauliZ(wires=['a']))"
        assert str(m) == expected


class TestExpansion:
    """Test for measurement expansion"""

    def test_expand_pauli(self):
        """Test the expansion of a Pauli observable"""
        obs = qml.PauliX(0) @ qml.PauliY(1)
        m = qml.expval(op=obs)
        tape = m.expand()

        assert len(tape.operations) == 4

        assert tape.operations[0].name == "Hadamard"
        assert tape.operations[0].wires.tolist() == [0]

        assert tape.operations[1].name == "PauliZ"
        assert tape.operations[1].wires.tolist() == [1]
        assert tape.operations[2].name == "S"
        assert tape.operations[2].wires.tolist() == [1]
        assert tape.operations[3].name == "Hadamard"
        assert tape.operations[3].wires.tolist() == [1]

        assert len(tape.measurements) == 1
        assert tape.measurements[0].return_type is Expectation
        assert tape.measurements[0].wires.tolist() == [0, 1]
        assert np.all(tape.measurements[0].eigvals() == np.array([1, -1, -1, 1]))

    def test_expand_hermitian(self, tol):
        """Test the expansion of an hermitian observable"""
        H = np.array([[1, 2], [2, 4]])
        obs = qml.Hermitian(H, wires=["a"])

        m = qml.expval(op=obs)
        tape = m.expand()

        assert len(tape.operations) == 1

        assert tape.operations[0].name == "QubitUnitary"
        assert tape.operations[0].wires.tolist() == ["a"]
        assert np.allclose(
            tape.operations[0].parameters[0],
            np.array([[-2, 1], [1, 2]]) / np.sqrt(5),
            atol=tol,
            rtol=0,
        )

        assert len(tape.measurements) == 1
        assert tape.measurements[0].return_type is Expectation
        assert tape.measurements[0].wires.tolist() == ["a"]
        assert np.all(tape.measurements[0].eigvals() == np.array([0, 5]))

    def test_expand_no_observable(self):
        """Check that an exception is raised if the measurement to
        be expanded has no observable"""
        with pytest.raises(DecompositionUndefinedError):
            ProbabilityMP(wires=qml.wires.Wires([0, 1])).expand()

    @pytest.mark.parametrize(
        "m",
        [
            ExpectationMP(obs=qml.PauliX(0) @ qml.PauliY(1)),
            VarianceMP(obs=qml.PauliX(0) @ qml.PauliY(1)),
            ProbabilityMP(obs=qml.PauliX(0) @ qml.PauliY(1)),
            ExpectationMP(obs=qml.PauliX(5)),
            VarianceMP(obs=qml.PauliZ(0) @ qml.Identity(3)),
            ProbabilityMP(obs=qml.PauliZ(0) @ qml.Identity(3)),
        ],
    )
    def test_has_decomposition_true_pauli(self, m):
        """Test that measurements of Paulis report to have a decomposition."""
        assert m.has_decomposition is True

    def test_has_decomposition_true_hermitian(self):
        """Test that measurements of Hermitians report to have a decomposition."""
        H = np.array([[1, 2], [2, 4]])
        obs = qml.Hermitian(H, wires=["a"])
        m = qml.expval(op=obs)
        assert m.has_decomposition is True

    def test_has_decomposition_false_hermitian_wo_diaggates(self):
        """Test that measurements of Hermitians report to have a decomposition."""

        class HermitianNoDiagGates(qml.Hermitian):
            @property
            def has_diagonalizing_gates(self):
                return False

        H = np.array([[1, 2], [2, 4]])
        obs = HermitianNoDiagGates(H, wires=["a"])
        m = ExpectationMP(obs=obs)
        assert m.has_decomposition is False

    def test_has_decomposition_false_no_observable(self):
        """Check a MeasurementProcess without observable to report not having a decomposition"""
        m = ProbabilityMP(wires=qml.wires.Wires([0, 1]))
        assert m.has_decomposition is False

        m = ExpectationMP(wires=qml.wires.Wires([0, 1]), eigvals=np.ones(4))
        assert m.has_decomposition is False

    @pytest.mark.parametrize(
        "m",
        [
            SampleMP(),
            SampleMP(wires=["a", 1]),
            CountsMP(all_outcomes=True),
            CountsMP(wires=["a", 1], all_outcomes=True),
            CountsMP(),
            CountsMP(wires=["a", 1]),
        ],
    )
    def test_samples_computational_basis_true(self, m):
        """Test that measurements of Paulis report to have a decomposition."""
        assert m.samples_computational_basis is True

    @pytest.mark.parametrize(
        "m",
        [
            ExpectationMP(obs=qml.PauliX(2)),
            VarianceMP(obs=qml.PauliX("a")),
            ProbabilityMP(obs=qml.PauliX("b")),
            ProbabilityMP(wires=["a", 1]),
            SampleMP(obs=qml.PauliX("a")),
            CountsMP(obs=qml.PauliX("a")),
            StateMP(),
            VnEntropyMP(wires=["a", 1]),
            MutualInfoMP(wires=[["a", 1], ["b", 2]]),
            ClassicalShadowMP(wires=[["a", 1], ["b", 2]]),
            ShadowExpvalMP(H=qml.PauliX("a")),
        ],
    )
    def test_samples_computational_basis_false(self, m):
        """Test that measurements of Paulis report to have a decomposition."""
        assert m.samples_computational_basis is False


class TestDiagonalizingGates:
    def test_no_expansion(self):
        """Test a measurement that has no expansion"""
        m = qml.sample()

        assert m.diagonalizing_gates() == []

    def test_obs_diagonalizing_gates(self):
        """Test diagonalizing_gates method with and observable."""
        m = qml.expval(qml.PauliY(0))

        res = m.diagonalizing_gates()

        assert len(res) == 3

        expected_classes = [qml.PauliZ, qml.S, qml.Hadamard]
        for op, c in zip(res, expected_classes):
            assert isinstance(op, c)


@pytest.mark.parametrize("switch_return", [qml.enable_return, qml.disable_return])
class TestSampleMeasurement:
    """Tests for the SampleMeasurement class."""

    def test_custom_sample_measurement(self, switch_return):
        """Test the execution of a custom sampled measurement."""
        switch_return()

        class MyMeasurement(SampleMeasurement):
            def process_samples(self, samples, wire_order, shot_range, bin_size):
                return qml.math.sum(samples[..., self.wires])

        dev = qml.device("default.qubit", wires=2, shots=1000)

        @qml.qnode(dev)
        def circuit():
            qml.PauliX(0)
            return MyMeasurement(wires=[0]), MyMeasurement(wires=[1])

        assert qml.math.allequal(circuit(), [1000, 0])

    def test_sample_measurement_without_shots(self, switch_return):
        """Test that executing a sampled measurement with ``shots=None`` raises an error."""
        switch_return()

        class MyMeasurement(SampleMeasurement):
            def process_samples(self, samples, wire_order, shot_range, bin_size):
                return qml.math.sum(samples[..., self.wires])

        dev = qml.device("default.qubit", wires=2)

        @qml.qnode(dev)
        def circuit():
            qml.PauliX(0)
            return MyMeasurement(wires=[0]), MyMeasurement(wires=[1])

        with pytest.raises(
            ValueError, match="Shots must be specified in the device to compute the measurement "
        ):
            circuit()

    def test_method_overriden_by_device(self, switch_return):
        """Test that the device can override a measurement process."""
        switch_return()

        class MyMeasurement(SampleMeasurement):
            method_name = "test_method"

            def process_samples(self, samples, wire_order, shot_range, bin_size):
                return qml.math.sum(samples[..., self.wires])

        dev = qml.device("default.qubit", wires=2, shots=1000)

        @qml.qnode(dev)
        def circuit():
            qml.PauliX(0)
            return MyMeasurement(wires=[0]), MyMeasurement(wires=[1])

        circuit.device.test_method = lambda obs, shot_range=None, bin_size=None: 2

        assert qml.math.allequal(circuit(), [2, 2])


@pytest.mark.parametrize("switch_return", [qml.enable_return, qml.disable_return])
class TestStateMeasurement:
    """Tests for the SampleMeasurement class."""

    def test_custom_state_measurement(self, switch_return):
        """Test the execution of a custom state measurement."""
        switch_return()

        class MyMeasurement(StateMeasurement):
            def process_state(self, state, wire_order):
                return qml.math.sum(state)

        dev = qml.device("default.qubit", wires=2)

        @qml.qnode(dev)
        def circuit():
            return MyMeasurement()

        assert circuit() == 1

    def test_sample_measurement_with_shots(self, switch_return):
        """Test that executing a state measurement with shots raises a warning."""
        switch_return()

        class MyMeasurement(StateMeasurement):
            def process_state(self, state, wire_order):
                return qml.math.sum(state)

        dev = qml.device("default.qubit", wires=2, shots=1000)

        @qml.qnode(dev)
        def circuit():
            return MyMeasurement()

        with pytest.warns(
            UserWarning,
            match="Requested measurement MyMeasurement with finite shots",
        ):
            circuit()

    def test_method_overriden_by_device(self, switch_return):
        """Test that the device can override a measurement process."""
        switch_return()

        class MyMeasurement(StateMeasurement):
            method_name = "test_method"

            def process_state(self, state, wire_order):
                return qml.math.sum(state)

        dev = qml.device("default.qubit", wires=2)

        @qml.qnode(dev)
        def circuit():
            return MyMeasurement()

        circuit.device.test_method = lambda obs, shot_range=None, bin_size=None: 2

        assert circuit() == 2


@pytest.mark.parametrize("switch_return", [qml.enable_return, qml.disable_return])
class TestMeasurementTransform:
    """Tests for the MeasurementTransform class."""

    def test_custom_measurement(self, switch_return):
        """Test the execution of a custom measurement."""
        switch_return()

        class MyMeasurement(MeasurementTransform):
            def process(self, qscript, device):
                return {device.shots: len(qscript)}

        dev = qml.device("default.qubit", wires=2, shots=1000)

        @qml.qnode(dev)
        def circuit():
            return MyMeasurement()

        assert circuit() == {dev.shots: len(circuit.tape)}

    def test_method_overriden_by_device(self, switch_return):
        """Test that the device can override a measurement process."""
        switch_return()

        class MyMeasurement(MeasurementTransform):
            method_name = "test_method"

            def process(self, qscript, device):
                return {device.shots: len(qscript)}

        dev = qml.device("default.qubit", wires=2)

        @qml.qnode(dev)
        def circuit():
            return MyMeasurement()

        circuit.device.test_method = lambda qscript: 2

        assert circuit() == 2
