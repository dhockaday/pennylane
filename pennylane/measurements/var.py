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
# pylint: disable=protected-access
"""
This module contains the qml.var measurement.
"""
import warnings
from typing import Sequence, Tuple

import pennylane as qml
from pennylane.operation import Operator
from pennylane.ops import Projector
from pennylane.wires import Wires

from .measurements import SampleMeasurement, StateMeasurement, Variance


def var(op: Operator):
    r"""Variance of the supplied observable.

    **Example:**

    .. code-block:: python3

        dev = qml.device("default.qubit", wires=2)

        @qml.qnode(dev)
        def circuit(x):
            qml.RX(x, wires=0)
            qml.Hadamard(wires=1)
            qml.CNOT(wires=[0, 1])
            return qml.var(qml.PauliY(0))

    Executing this QNode:

    >>> circuit(0.5)
    0.7701511529340698

    Args:
        op (Observable): a quantum observable object

    Raises:
        QuantumFunctionError: `op` is not an instance of :class:`~.Observable`
    """
    if not op.is_hermitian:
        warnings.warn(f"{op.name} might not be hermitian.")
    return VarianceMP(obs=op)


class VarianceMP(SampleMeasurement, StateMeasurement):
    """Measurement process that computes the variance of the supplied observable."""

    method_name = "var"

    @property
    def return_type(self):
        return Variance

    @property
    def numeric_type(self):
        return float

    def shape(self, device=None):
        if qml.active_return():
            return self._shape_new(device)
        if device is None or device.shot_vector is None:
            return (1,)
        num_shot_elements = sum(s.copies for s in device.shot_vector)
        return (num_shot_elements,)

    def _shape_new(self, device=None):
        if device is None or device.shot_vector is None:
            return ()
        num_shot_elements = sum(s.copies for s in device.shot_vector)
        return tuple(() for _ in range(num_shot_elements))

    def process_samples(
        self,
        samples: Sequence[complex],
        wire_order: Wires,
        shot_range: Tuple[int] = None,
        bin_size: int = None,
    ):
        if isinstance(self.obs, Projector):
            # branch specifically to handle the projector observable
            idx = int("".join(str(i) for i in self.obs.parameters[0]), 2)
            # we use ``self.wires`` instead of ``self.obs`` because the observable was
            # already applied before the sampling
            probs = qml.probs(wires=self.wires).process_samples(
                samples=samples, wire_order=wire_order, shot_range=shot_range, bin_size=bin_size
            )
            return probs[idx] - probs[idx] ** 2

        # estimate the variance
        samples = qml.sample(op=self.obs).process_samples(
            samples=samples, wire_order=wire_order, shot_range=shot_range, bin_size=bin_size
        )
        # With broadcasting, we want to take the variance over axis 1, which is the -1st/-2nd with/
        # without bin_size. Without broadcasting, axis 0 is the -1st/-2nd with/without bin_size
        axis = -1 if bin_size is None else -2
        # TODO: do we need to squeeze here? Maybe remove with new return types
        return qml.math.squeeze(qml.math.var(samples, axis=axis))

    def process_state(self, state: Sequence[complex], wire_order: Wires):
        if isinstance(self.obs, Projector):
            # branch specifically to handle the projector observable
            idx = int("".join(str(i) for i in self.obs.parameters[0]), 2)
            # we use ``self.wires`` instead of ``self.obs`` because the observable was
            # already applied to the state
            probs = qml.probs(wires=self.wires).process_state(state=state, wire_order=wire_order)
            return probs[idx] - probs[idx] ** 2

        eigvals = qml.math.asarray(self.obs.eigvals(), dtype=float)

        # the probability vector must be permuted to account for the permuted wire order of the observable
        new_obs_wires = self._permute_wires(self.obs.wires)
        # we use ``wires`` instead of ``op`` because the observable was
        # already applied to the state
        prob = qml.probs(wires=new_obs_wires).process_state(state=state, wire_order=wire_order)
        # In case of broadcasting, `prob` has two axes and these are a matrix-vector products
        return qml.math.dot(prob, (eigvals**2)) - qml.math.dot(prob, eigvals) ** 2
