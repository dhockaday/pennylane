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
import pytest
import pennylane as qml
import numpy as np
from scipy.stats import unitary_group


class TestDecomposition:
    """Tests that the template defines the correct decomposition."""

    def test_expected_qscript(self):
        """Tests if QuantumPhaseEstimation populates the quantum script as expected for a fixed example"""

        m = qml.RX(0.3, wires=0).matrix()

        op = qml.QuantumPhaseEstimation(m, target_wires=[0], estimation_wires=[1, 2])
        qscript = op.expand()

        with qml.queuing.AnnotatedQueue() as q:
            qml.Hadamard(1),
            qml.ControlledQubitUnitary(m @ m, control_wires=[1], wires=[0]),
            qml.Hadamard(2),
            qml.ControlledQubitUnitary(m, control_wires=[2], wires=[0]),
            qml.adjoint(qml.QFT(wires=[1, 2]))
        qscript2 = qml.tape.QuantumScript.from_queue(q)

        assert len(qscript) == len(qscript2)
        # qml.equal doesn't work for Adjoint op yet, so we stop before we get to it.
        for op1, op2 in zip(qscript[:-1], qscript2[:-1]):
            assert qml.equal(op1, op2)

        assert isinstance(qscript[-1], qml.ops.op_math.Adjoint)
        assert qml.equal(qscript[-1].base, qml.QFT(wires=(1, 2)))

        assert np.allclose(qscript[1].matrix(), qscript[1].matrix())
        assert np.allclose(qscript[3].matrix(), qscript[3].matrix())

    @pytest.mark.parametrize("phase", [2, 3, 6, np.pi])
    def test_phase_estimated(self, phase):
        """Tests that the QPE circuit can correctly estimate the phase of a simple RX rotation."""
        estimates = []
        wire_range = range(2, 10)

        for wires in wire_range:
            dev = qml.device("default.qubit", wires=wires)
            m = qml.RX(phase, wires=0).matrix()
            target_wires = [0]
            estimation_wires = range(1, wires)

            with qml.queuing.AnnotatedQueue() as q:
                # We want to prepare an eigenstate of RX, in this case |+>
                qml.Hadamard(wires=target_wires)

                qml.QuantumPhaseEstimation(
                    m, target_wires=target_wires, estimation_wires=estimation_wires
                )
                qml.probs(estimation_wires)

            tape = qml.tape.QuantumScript.from_queue(q)
            tape = tape.expand(depth=2, stop_at=lambda obj: obj.name in dev.operations)

            res = dev.execute(tape).flatten()
            initial_estimate = np.argmax(res) / 2 ** (wires - 1)

            # We need to rescale because RX is exp(- i theta X / 2) and we expect a unitary of the
            # form exp(2 pi i theta X)
            rescaled_estimate = (1 - initial_estimate) * np.pi * 4
            estimates.append(rescaled_estimate)

        # Check that the error is monotonically decreasing
        for i in range(len(estimates) - 1):
            err1 = np.abs(estimates[i] - phase)
            err2 = np.abs(estimates[i + 1] - phase)
            assert err1 >= err2

        # This is quite a large error, but we'd need to push the qubit number up more to get it
        # lower
        assert np.allclose(estimates[-1], phase, rtol=1e-2)

    def test_phase_estimated_two_qubit(self):
        """Tests that the QPE circuit can correctly estimate the phase of a random two-qubit
        unitary."""

        unitary = unitary_group.rvs(4, random_state=1967)
        eigvals, eigvecs = np.linalg.eig(unitary)

        state = eigvecs[:, 0]
        eigval = eigvals[0]
        phase = np.real_if_close(np.log(eigval) / (2 * np.pi * 1j))

        estimates = []
        wire_range = range(3, 11)

        for wires in wire_range:
            dev = qml.device("default.qubit", wires=wires)

            target_wires = [0, 1]
            estimation_wires = range(2, wires)

            with qml.queuing.AnnotatedQueue() as q:
                # We want to prepare an eigenstate of RX, in this case |+>
                qml.QubitStateVector(state, wires=target_wires)

                qml.QuantumPhaseEstimation(
                    unitary, target_wires=target_wires, estimation_wires=estimation_wires
                )
                qml.probs(estimation_wires)

            tape = qml.tape.QuantumScript.from_queue(q)
            tape = tape.expand(depth=2, stop_at=lambda obj: obj.name in dev.operations)
            res = dev.execute(tape).flatten()

            if phase < 0:
                estimate = np.argmax(res) / 2 ** (wires - 2) - 1
            else:
                estimate = np.argmax(res) / 2 ** (wires - 2)
            estimates.append(estimate)

        # Check that the error is monotonically decreasing
        for i in range(len(estimates) - 1):
            err1 = np.abs(estimates[i] - phase)
            err2 = np.abs(estimates[i + 1] - phase)
            assert err1 >= err2

        # This is quite a large error, but we'd need to push the qubit number up more to get it
        # lower
        assert np.allclose(estimates[-1], phase, rtol=1e-2)

    def test_adjoint(self):
        """Test that the QPE adjoint works."""
        dev = qml.device("default.qubit", wires=3)

        @qml.qnode(dev)
        def qpe_circuit():

            qml.Hadamard(wires=0)
            qml.PauliX(wires=1)
            qml.QuantumPhaseEstimation(
                qml.PauliX.compute_matrix(),
                target_wires=[0],
                estimation_wires=[1, 2],
            )

            qml.adjoint(qml.QuantumPhaseEstimation)(
                qml.PauliX.compute_matrix(),
                target_wires=[0],
                estimation_wires=[1, 2],
            )
            qml.Hadamard(wires=0)
            qml.PauliX(wires=1)

            return qml.state()

        assert qml.math.isclose(qpe_circuit()[0], 1)


class TestInputs:
    """Test inputs and pre-processing."""

    def test_same_wires(self):
        """Tests if a QuantumFunctionError is raised if target_wires and estimation_wires contain a
        common element"""

        with pytest.raises(qml.QuantumFunctionError, match="The target wires and estimation wires"):
            qml.QuantumPhaseEstimation(np.eye(2), target_wires=[0, 1], estimation_wires=[1, 2])

    def test_id(self):
        """Tests that the id attribute can be set."""
        template = qml.QuantumPhaseEstimation(
            np.eye(2), target_wires=[0, 1], estimation_wires=[2, 3], id="a"
        )
        assert template.id == "a"
