import numpy as np
import numba
import umap.distances as dist
from umap.utils import tau_rand_int


@numba.njit()
def clip(val):
    """Standard clamping of a value into a fixed range (in this case -4.0 to
    4.0)

    Parameters
    ----------
    val: float
        The value to be clamped.

    Returns
    -------
    The clamped value, now fixed to be in the range -4.0 to 4.0.
    """
    if val > 4.0:
        return 4.0
    elif val < -4.0:
        return -4.0
    else:
        return val


@numba.njit(
    "f4(f4[::1],f4[::1])",
    fastmath=True,
    cache=True,
    locals={
        "result": numba.types.float32,
        "diff": numba.types.float32,
        "dim": numba.types.int32,
    },
)
def rdist(x, y):
    """Reduced Euclidean distance.

    Parameters
    ----------
    x: array of shape (embedding_dim,)
    y: array of shape (embedding_dim,)

    Returns
    -------
    The squared euclidean distance between x and y
    """
    result = 0.0
    dim = x.shape[0]
    for i in range(dim):
        diff = x[i] - y[i]
        result += diff * diff

    return result


def _optimize_layout_euclidean_single_epoch(
    head_embedding,
    tail_embedding,
    head,
    tail,
    n_vertices,
    epochs_per_sample,
    a,
    b,
    rng_state,
    gamma,
    dim,
    move_other,
    alpha,
    epochs_per_negative_sample,
    epoch_of_next_negative_sample,
    epoch_of_next_sample,
    n,
):
    for i in numba.prange(epochs_per_sample.shape[0]):
        if epoch_of_next_sample[i] <= n:
            j = head[i]
            k = tail[i]

            current = head_embedding[j]
            other = tail_embedding[k]

            dist_squared = rdist(current, other)

            if dist_squared > 0.0:
                grad_coeff = -2.0 * a * b * pow(dist_squared, b - 1.0)
                grad_coeff /= a * pow(dist_squared, b) + 1.0
            else:
                grad_coeff = 0.0

            for d in range(dim):
                grad_d = clip(grad_coeff * (current[d] - other[d]))
                current[d] += grad_d * alpha
                if move_other:
                    other[d] += -grad_d * alpha

            epoch_of_next_sample[i] += epochs_per_sample[i]

            n_neg_samples = int(
                (n - epoch_of_next_negative_sample[i]) / epochs_per_negative_sample[i]
            )

            for p in range(n_neg_samples):
                k = tau_rand_int(rng_state) % n_vertices

                other = tail_embedding[k]

                dist_squared = rdist(current, other)

                if dist_squared > 0.0:
                    grad_coeff = 2.0 * gamma * b
                    grad_coeff /= (0.001 + dist_squared) * (
                        a * pow(dist_squared, b) + 1
                    )
                elif j == k:
                    continue
                else:
                    grad_coeff = 0.0

                for d in range(dim):
                    if grad_coeff > 0.0:
                        grad_d = clip(grad_coeff * (current[d] - other[d]))
                    else:
                        grad_d = 4.0
                    current[d] += grad_d * alpha

            epoch_of_next_negative_sample[i] += (
                n_neg_samples * epochs_per_negative_sample[i]
            )


def optimize_layout_euclidean(
    head_embedding,
    tail_embedding,
    head,
    tail,
    n_epochs,
    n_vertices,
    epochs_per_sample,
    a,
    b,
    rng_state,
    gamma=1.0,
    initial_alpha=1.0,
    negative_sample_rate=5.0,
    parallel=False,
    verbose=False,
):
    """Improve an embedding using stochastic gradient descent to minimize the
    fuzzy set cross entropy between the 1-skeletons of the high dimensional
    and low dimensional fuzzy simplicial sets. In practice this is done by
    sampling edges based on their membership strength (with the (1-p) terms
    coming from negative sampling similar to word2vec).
    Parameters
    ----------
    head_embedding: array of shape (n_samples, n_components)
        The initial embedding to be improved by SGD.
    tail_embedding: array of shape (source_samples, n_components)
        The reference embedding of embedded points. If not embedding new
        previously unseen points with respect to an existing embedding this
        is simply the head_embedding (again); otherwise it provides the
        existing embedding to embed with respect to.
    head: array of shape (n_1_simplices)
        The indices of the heads of 1-simplices with non-zero membership.
    tail: array of shape (n_1_simplices)
        The indices of the tails of 1-simplices with non-zero membership.
    n_epochs: int
        The number of training epochs to use in optimization.
    n_vertices: int
        The number of vertices (0-simplices) in the dataset.
    epochs_per_samples: array of shape (n_1_simplices)
        A float value of the number of epochs per 1-simplex. 1-simplices with
        weaker membership strength will have more epochs between being sampled.
    a: float
        Parameter of differentiable approximation of right adjoint functor
    b: float
        Parameter of differentiable approximation of right adjoint functor
    rng_state: array of int64, shape (3,)
        The internal state of the rng
    gamma: float (optional, default 1.0)
        Weight to apply to negative samples.
    initial_alpha: float (optional, default 1.0)
        Initial learning rate for the SGD.
    negative_sample_rate: int (optional, default 5)
        Number of negative samples to use per positive sample.
    parallel: bool (optional, default False)
        Whether to run the computation using numba parallel.
        Running in parallel is non-deterministic, and is not used
        if a random seed has been set, to ensure reproducibility.
    verbose: bool (optional, default False)
        Whether to report information on the current progress of the algorithm.
    Returns
    -------
    embedding: array of shape (n_samples, n_components)
        The optimized embedding.
    """

    dim = head_embedding.shape[1]
    move_other = head_embedding.shape[0] == tail_embedding.shape[0]
    alpha = initial_alpha

    epochs_per_negative_sample = epochs_per_sample / negative_sample_rate
    epoch_of_next_negative_sample = epochs_per_negative_sample.copy()
    epoch_of_next_sample = epochs_per_sample.copy()

    optimize_fn = numba.njit(
        _optimize_layout_euclidean_single_epoch, fastmath=True, parallel=parallel
    )
    for n in range(n_epochs):
        optimize_fn(
            head_embedding,
            tail_embedding,
            head,
            tail,
            n_vertices,
            epochs_per_sample,
            a,
            b,
            rng_state,
            gamma,
            dim,
            move_other,
            alpha,
            epochs_per_negative_sample,
            epoch_of_next_negative_sample,
            epoch_of_next_sample,
            n,
        )

        alpha = initial_alpha * (1.0 - (float(n) / float(n_epochs)))

        if verbose and n % int(n_epochs / 10) == 0:
            print("\tcompleted ", n, " / ", n_epochs, "epochs")

    return head_embedding


@numba.njit(fastmath=True)
def optimize_layout_generic(
    head_embedding,
    tail_embedding,
    head,
    tail,
    n_epochs,
    n_vertices,
    epochs_per_sample,
    a,
    b,
    rng_state,
    gamma=1.0,
    initial_alpha=1.0,
    negative_sample_rate=5.0,
    output_metric=dist.euclidean,
    output_metric_kwds=(),
    verbose=False,
):
    """Improve an embedding using stochastic gradient descent to minimize the
    fuzzy set cross entropy between the 1-skeletons of the high dimensional
    and low dimensional fuzzy simplicial sets. In practice this is done by
    sampling edges based on their membership strength (with the (1-p) terms
    coming from negative sampling similar to word2vec).

    Parameters
    ----------
    head_embedding: array of shape (n_samples, n_components)
        The initial embedding to be improved by SGD.

    tail_embedding: array of shape (source_samples, n_components)
        The reference embedding of embedded points. If not embedding new
        previously unseen points with respect to an existing embedding this
        is simply the head_embedding (again); otherwise it provides the
        existing embedding to embed with respect to.

    head: array of shape (n_1_simplices)
        The indices of the heads of 1-simplices with non-zero membership.

    tail: array of shape (n_1_simplices)
        The indices of the tails of 1-simplices with non-zero membership.

    weight: array of shape (n_1_simplices)
        The membership weights of the 1-simplices.

    n_epochs: int
        The number of training epochs to use in optimization.

    n_vertices: int
        The number of vertices (0-simplices) in the dataset.

    epochs_per_sample: array of shape (n_1_simplices)
        A float value of the number of epochs per 1-simplex. 1-simplices with
        weaker membership strength will have more epochs between being sampled.

    a: float
        Parameter of differentiable approximation of right adjoint functor

    b: float
        Parameter of differentiable approximation of right adjoint functor

    rng_state: array of int64, shape (3,)
        The internal state of the rng

    gamma: float (optional, default 1.0)
        Weight to apply to negative samples.

    initial_alpha: float (optional, default 1.0)
        Initial learning rate for the SGD.

    negative_sample_rate: int (optional, default 5)
        Number of negative samples to use per positive sample.

    verbose: bool (optional, default False)
        Whether to report information on the current progress of the algorithm.

    Returns
    -------
    embedding: array of shape (n_samples, n_components)
        The optimized embedding.
    """

    dim = head_embedding.shape[1]
    move_other = head_embedding.shape[0] == tail_embedding.shape[0]
    alpha = initial_alpha

    epochs_per_negative_sample = epochs_per_sample / negative_sample_rate
    epoch_of_next_negative_sample = epochs_per_negative_sample.copy()
    epoch_of_next_sample = epochs_per_sample.copy()

    for n in range(n_epochs):
        for i in range(epochs_per_sample.shape[0]):
            if epoch_of_next_sample[i] <= n:
                j = head[i]
                k = tail[i]

                current = head_embedding[j]
                other = tail_embedding[k]

                dist_output, grad_dist_output = output_metric(
                    current, other, *output_metric_kwds
                )
                _, rev_grad_dist_output = output_metric(
                    other, current, *output_metric_kwds
                )

                if dist_output > 0.0:
                    w_l = pow((1 + a * pow(dist_output, 2 * b)), -1)
                else:
                    w_l = 1.0
                grad_coeff = 2 * b * (w_l - 1) / (dist_output + 1e-6)

                for d in range(dim):
                    grad_d = clip(grad_coeff * grad_dist_output[d])

                    current[d] += grad_d * alpha
                    if move_other:
                        grad_d = clip(grad_coeff * rev_grad_dist_output[d])
                        other[d] += grad_d * alpha

                epoch_of_next_sample[i] += epochs_per_sample[i]

                n_neg_samples = int(
                    (n - epoch_of_next_negative_sample[i])
                    / epochs_per_negative_sample[i]
                )

                for p in range(n_neg_samples):
                    k = tau_rand_int(rng_state) % n_vertices

                    other = tail_embedding[k]

                    dist_output, grad_dist_output = output_metric(
                        current, other, *output_metric_kwds
                    )

                    if dist_output > 0.0:
                        w_l = pow((1 + a * pow(dist_output, 2 * b)), -1)
                    elif j == k:
                        continue
                    else:
                        w_l = 1.0

                    grad_coeff = gamma * 2 * b * w_l / (dist_output + 1e-6)

                    for d in range(dim):
                        grad_d = clip(grad_coeff * grad_dist_output[d])
                        current[d] += grad_d * alpha

                epoch_of_next_negative_sample[i] += (
                    n_neg_samples * epochs_per_negative_sample[i]
                )

        alpha = initial_alpha * (1.0 - (float(n) / float(n_epochs)))

        if verbose and n % int(n_epochs / 10) == 0:
            print("\tcompleted ", n, " / ", n_epochs, "epochs")

    return head_embedding


@numba.njit(fastmath=True)
def optimize_layout_inverse(
    head_embedding,
    tail_embedding,
    head,
    tail,
    weight,
    sigmas,
    rhos,
    n_epochs,
    n_vertices,
    epochs_per_sample,
    a,
    b,
    rng_state,
    gamma=1.0,
    initial_alpha=1.0,
    negative_sample_rate=5.0,
    output_metric=dist.euclidean,
    output_metric_kwds=(),
    verbose=False,
):
    """Improve an embedding using stochastic gradient descent to minimize the
    fuzzy set cross entropy between the 1-skeletons of the high dimensional
    and low dimensional fuzzy simplicial sets. In practice this is done by
    sampling edges based on their membership strength (with the (1-p) terms
    coming from negative sampling similar to word2vec).

    Parameters
    ----------
    head_embedding: array of shape (n_samples, n_components)
        The initial embedding to be improved by SGD.

    tail_embedding: array of shape (source_samples, n_components)
        The reference embedding of embedded points. If not embedding new
        previously unseen points with respect to an existing embedding this
        is simply the head_embedding (again); otherwise it provides the
        existing embedding to embed with respect to.

    head: array of shape (n_1_simplices)
        The indices of the heads of 1-simplices with non-zero membership.

    tail: array of shape (n_1_simplices)
        The indices of the tails of 1-simplices with non-zero membership.

    weight: array of shape (n_1_simplices)
        The membership weights of the 1-simplices.

    n_epochs: int
        The number of training epochs to use in optimization.

    n_vertices: int
        The number of vertices (0-simplices) in the dataset.

    epochs_per_sample: array of shape (n_1_simplices)
        A float value of the number of epochs per 1-simplex. 1-simplices with
        weaker membership strength will have more epochs between being sampled.

    a: float
        Parameter of differentiable approximation of right adjoint functor

    b: float
        Parameter of differentiable approximation of right adjoint functor

    rng_state: array of int64, shape (3,)
        The internal state of the rng

    gamma: float (optional, default 1.0)
        Weight to apply to negative samples.

    initial_alpha: float (optional, default 1.0)
        Initial learning rate for the SGD.

    negative_sample_rate: int (optional, default 5)
        Number of negative samples to use per positive sample.

    verbose: bool (optional, default False)
        Whether to report information on the current progress of the algorithm.

    Returns
    -------
    embedding: array of shape (n_samples, n_components)
        The optimized embedding.
    """

    dim = head_embedding.shape[1]
    move_other = head_embedding.shape[0] == tail_embedding.shape[0]
    alpha = initial_alpha

    epochs_per_negative_sample = epochs_per_sample / negative_sample_rate
    epoch_of_next_negative_sample = epochs_per_negative_sample.copy()
    epoch_of_next_sample = epochs_per_sample.copy()

    for n in range(n_epochs):
        for i in range(epochs_per_sample.shape[0]):
            if epoch_of_next_sample[i] <= n:
                j = head[i]
                k = tail[i]

                current = head_embedding[j]
                other = tail_embedding[k]

                dist_output, grad_dist_output = output_metric(
                    current, other, *output_metric_kwds
                )

                w_l = weight[i]
                grad_coeff = -(1 / (w_l * sigmas[k] + 1e-6))

                for d in range(dim):
                    grad_d = clip(grad_coeff * grad_dist_output[d])

                    current[d] += grad_d * alpha
                    if move_other:
                        other[d] += -grad_d * alpha

                epoch_of_next_sample[i] += epochs_per_sample[i]

                n_neg_samples = int(
                    (n - epoch_of_next_negative_sample[i])
                    / epochs_per_negative_sample[i]
                )

                for p in range(n_neg_samples):
                    k = tau_rand_int(rng_state) % n_vertices

                    other = tail_embedding[k]

                    dist_output, grad_dist_output = output_metric(
                        current, other, *output_metric_kwds
                    )

                    # w_l = 0.0 # for negative samples, the edge does not exist
                    w_h = np.exp(-max(dist_output - rhos[k], 1e-6) / (sigmas[k] + 1e-6))
                    grad_coeff = -gamma * ((0 - w_h) / ((1 - w_h) * sigmas[k] + 1e-6))

                    for d in range(dim):
                        grad_d = clip(grad_coeff * grad_dist_output[d])
                        current[d] += grad_d * alpha

                epoch_of_next_negative_sample[i] += (
                    n_neg_samples * epochs_per_negative_sample[i]
                )

        alpha = initial_alpha * (1.0 - (float(n) / float(n_epochs)))

        if verbose and n % int(n_epochs / 10) == 0:
            print("\tcompleted ", n, " / ", n_epochs, "epochs")

    return head_embedding


def _optimize_layout_aligned_euclidean_single_epoch(
    head_embeddings,
    tail_embeddings,
    heads,
    tails,
    epochs_per_sample,
    a,
    b,
    regularisation_weights,
    relations,
    rng_state,
    gamma,
    lambda_,
    dim,
    move_other,
    alpha,
    epochs_per_negative_sample,
    epoch_of_next_negative_sample,
    epoch_of_next_sample,
    n,
):
    n_embeddings = len(heads)
    window_size = (relations.shape[1] - 1) // 2

    max_n_edges = 0
    for e_p_s in epochs_per_sample:
        if e_p_s.shape[0] >= max_n_edges:
            max_n_edges = e_p_s.shape[0]

    embedding_order = np.arange(n_embeddings).astype(np.int32)
    np.random.shuffle(embedding_order)

    for i in range(max_n_edges):
        for m in embedding_order:
            if i < epoch_of_next_sample[m].shape[0] and epoch_of_next_sample[m][i] <= n:
                j = heads[m][i]
                k = tails[m][i]

                current = head_embeddings[m][j]
                other = tail_embeddings[m][k]

                dist_squared = rdist(current, other)

                if dist_squared > 0.0:
                    grad_coeff = -2.0 * a * b * pow(dist_squared, b - 1.0)
                    grad_coeff /= a * pow(dist_squared, b) + 1.0
                else:
                    grad_coeff = 0.0

                for d in range(dim):
                    grad_d = clip(grad_coeff * (current[d] - other[d]))

                    for offset in range(-window_size, window_size):
                        neighbor_m = m + offset
                        if (
                                neighbor_m >= 0
                                and neighbor_m < n_embeddings
                                and offset != 0
                        ):
                            identified_index = relations[m, offset + window_size, j]
                            if identified_index >= 0:
                                grad_d -= clip(
                                    (lambda_ * np.exp(-(np.abs(offset) - 1)))
                                    * regularisation_weights[m, offset + window_size, j]
                                    * (
                                            current[d]
                                            - head_embeddings[neighbor_m][
                                                identified_index, d
                                            ]
                                    )
                                )

                    current[d] += clip(grad_d) * alpha
                    if move_other:
                        other_grad_d = clip(grad_coeff * (other[d] - current[d]))

                        for offset in range(-window_size, window_size):
                            neighbor_m = m + offset
                            if (
                                    neighbor_m >= 0
                                    and neighbor_m < n_embeddings
                                    and offset != 0
                            ):
                                identified_index = relations[m, offset + window_size, k]
                                if identified_index >= 0:
                                    grad_d -= clip(
                                        (lambda_ * np.exp(-(np.abs(offset) - 1)))
                                        * regularisation_weights[
                                            m, offset + window_size, k
                                        ]
                                        * (
                                                other[d]
                                                - head_embeddings[neighbor_m][
                                                    identified_index, d
                                                ]
                                        )
                                    )

                        other[d] += clip(other_grad_d) * alpha

                epoch_of_next_sample[m][i] += epochs_per_sample[m][i]

                if epochs_per_negative_sample[m][i] > 0:
                    n_neg_samples = int(
                        (n - epoch_of_next_negative_sample[m][i])
                        / epochs_per_negative_sample[m][i]
                    )
                else:
                    n_neg_samples = 0

                for p in range(n_neg_samples):
                    k = tau_rand_int(rng_state) % tail_embeddings[m].shape[0]

                    other = tail_embeddings[m][k]

                    dist_squared = rdist(current, other)

                    if dist_squared > 0.0:
                        grad_coeff = 2.0 * gamma * b
                        grad_coeff /= (0.001 + dist_squared) * (
                            a * pow(dist_squared, b) + 1
                        )
                    elif j == k:
                        continue
                    else:
                        grad_coeff = 0.0

                    for d in range(dim):
                        if grad_coeff > 0.0:
                            grad_d = clip(grad_coeff * (current[d] - other[d]))
                        else:
                            grad_d = 4.0

                        for offset in range(-window_size, window_size):
                            neighbor_m = m + offset
                            if (
                                    neighbor_m >= 0
                                    and neighbor_m < n_embeddings
                                    and offset != 0
                            ):
                                identified_index = relations[m, offset + window_size, j]
                                if identified_index >= 0:
                                    grad_d -= clip(
                                        (lambda_ * np.exp(-(np.abs(offset) - 1)))
                                        * regularisation_weights[
                                            m, offset + window_size, j
                                        ]
                                        * (
                                                current[d]
                                                - head_embeddings[neighbor_m][
                                                    identified_index, d
                                                ]
                                        )
                                    )

                        current[d] += clip(grad_d) * alpha

                epoch_of_next_negative_sample[m][i] += (
                    n_neg_samples * epochs_per_negative_sample[m][i]
                )


def optimize_layout_aligned_euclidean(
    head_embeddings,
    tail_embeddings,
    heads,
    tails,
    n_epochs,
    epochs_per_sample,
    regularisation_weights,
    relations,
    rng_state,
    a=1.576943460405378,
    b=0.8950608781227859,
    gamma=1.0,
    lambda_=5e-3,
    initial_alpha=1.0,
    negative_sample_rate=5.0,
    parallel=True,
    verbose=False,
):
    dim = head_embeddings[0].shape[1]
    move_other = head_embeddings[0].shape[0] == tail_embeddings[0].shape[0]
    alpha = initial_alpha

    epochs_per_negative_sample = numba.typed.List.empty_list(numba.types.float32[::1])
    epoch_of_next_negative_sample = numba.typed.List.empty_list(
        numba.types.float32[::1]
    )
    epoch_of_next_sample = numba.typed.List.empty_list(numba.types.float32[::1])

    for m in range(len(heads)):
        epochs_per_negative_sample.append(
            epochs_per_sample[m].astype(np.float32) / negative_sample_rate
        )
        epoch_of_next_negative_sample.append(
            epochs_per_negative_sample[m].astype(np.float32)
        )
        epoch_of_next_sample.append(epochs_per_sample[m].astype(np.float32))

    optimize_fn = numba.njit(
        _optimize_layout_aligned_euclidean_single_epoch,
        fastmath=True,
        parallel=parallel,
    )

    for n in range(n_epochs):
        optimize_fn(
            head_embeddings,
            tail_embeddings,
            heads,
            tails,
            epochs_per_sample,
            a,
            b,
            regularisation_weights,
            relations,
            rng_state,
            gamma,
            lambda_,
            dim,
            move_other,
            alpha,
            epochs_per_negative_sample,
            epoch_of_next_negative_sample,
            epoch_of_next_sample,
            n,
        )

        alpha = initial_alpha * (1.0 - (float(n) / float(n_epochs)))

        if verbose and n % int(n_epochs / 10) == 0:
            print("\tcompleted ", n, " / ", n_epochs, "epochs")

    return head_embeddings