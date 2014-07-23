try:
    import networkx as nx
except ImportError:
    import warnings
    warnings.warn('"cut_threshold" requires networkx')
import numpy as np
from . import _ncut
from . import _ncut_cy
from scipy.sparse import linalg
from scipy.sparse.linalg.eigen.arpack.arpack import ArpackNoConvergence
from scipy.sparse.linalg.eigen.arpack.arpack import ArpackError


def cut_threshold(labels, rag, thresh):
    """Combine regions seperated by weight less than threshold.

    Given an image's labels and its RAG, output new labels by
    combining regions whose nodes are seperated by a weight less
    than the given threshold.

    Parameters
    ----------
    labels : ndarray
        The array of labels.
    rag : RAG
        The region adjacency graph.
    thresh : float
        The threshold. Regions connected by edges with smaller weights are
        combined.

    Returns
    -------
    out : ndarray
        The new labelled array.

    Examples
    --------
    >>> from skimage import data, graph, segmentation
    >>> img = data.lena()
    >>> labels = segmentation.slic(img)
    >>> rag = graph.rag_mean_color(img, labels)
    >>> new_labels = graph.cut_threshold(labels, rag, 10)

    References
    ----------
    .. [1] Alain Tremeau and Philippe Colantoni
           "Regions Adjacency Graph Applied To Color Image Segmentation"
           http://citeseerx.ist.psu.edu/viewdoc/summary?doi=10.1.1.11.5274

    """
    # Because deleting edges while iterating through them produces an error.
    to_remove = [(x, y) for x, y, d in rag.edges_iter(data=True)
                 if d['weight'] >= thresh]
    rag.remove_edges_from(to_remove)

    comps = nx.connected_components(rag)

    # We construct an array which can map old labels to the new ones.
    # All the labels within a connected component are assigned to a single
    # label in the output.
    map_array = np.arange(labels.max() + 1, dtype=labels.dtype)
    for i, nodes in enumerate(comps):
        for node in nodes:
            for label in rag.node[node]['labels']:
                map_array[label] = i

    return map_array[labels]


def cut_n(labels, rag, thresh=0.001, num_cuts=10):
    """Perform Normalized Graph cut on the Region Adjacency Graph.

    Given an image's labels and its similarity RAG, recursively perform
    a 2-way normalized cut on it. All nodes belonging to a subgraph
    which cannot be cut further, are assigned a unique label in the
    output.

    Parameters
    ----------
    labels : ndarray
        The array of labels.
    rag : RAG
        The region adjacency graph.
    thresh : float
        The threshold. A subgraph won't be further subdivided if the
        value of the N-cut exceeds `thresh`.
    num_cuts : int
        The number or N-cuts to perform before determining the optimal one.

    Returns
    -------
    out : ndarray
        The new labelled array.

    Examples
    --------
    >>> from skimage import data, graph, segmentation, color, io
    >>> img = data.lena()
    >>> labels = segmentation.slic(img, compactness=30, n_segments=400)
    >>> rag = graph.rag_mean_color(img, labels, mode='similarity')
    >>> new_labels = graph.cut_n(labels, rag)

    References
    ----------
    .. [1] Shi, J.; Malik, J., "Normalized cuts and image segmentation",
           Pattern Analysis and Machine Intelligence,
           IEEE Transactions on , vol.22, no.8, pp.888,905, Aug 2000

    """
    _ncut_relabel(rag, thresh, num_cuts)

    from_ = range(labels.max() + 1)
    to = [rag.node[x]['ncut label'] for x in from_]
    map_array = np.array(to)

    return map_array[labels]


def _ncut_relabel(rag, thresh, num_cuts):
    """Perform Normalized Graph cut on the Region Adjacency Graph.

    Recursively partition the graph into 2, untill further subdividing
    yields a cut greather than `thresh` or such a cut cannot be computed.
    For such a subgraph, assign a 'ncut label` attribute to all its nodes,
    which is a their new unique label.

    Parameters
    ----------
    labels : ndarray
        The array of labels.
    rag : RAG
        The region adjacency graph.
    thresh : float
        The threshold. A subgraph won't be further subdivided if the
        value of the N-cut exceeds `thresh`.
    num_cuts : int
        The number or N-cuts to perform before determining the optimal one.
    """
    d, w = _ncut.DW_matrix(rag)
    error = False
    

    try:
        m = w.shape[0]
        d2 = d.copy()
        # Since d is diagonal, we can directly operate on it's data
        # the inverse
        d2.data = 1.0/d2.data
        # the square root
        d2.data = np.sqrt(d2.data)
        vals, vectors = linalg.eigsh(d2*(d - w)*d2, which='SM',
                                     k=min(100, m - 2))
    except ArpackNoConvergence as e:
        # Not all eigenvectors converged, salvage the remaining.
        vals = e.eigenvalues
        vectors = e.eigenvectors
        if len(vals) == 0:
            # No eigenvector converged.
            error = True
    except ValueError:
        # k is too less, happens when the graph is of size 1
        error = True
    except ArpackError:
        # Arpack failing when two eigenvectors are same
        error = True

    if not error:
        vals, vectors = np.real(vals), np.real(vectors)
        index2 = _ncut_cy.argmin2(vals)

        ev = np.real(vectors[:, index2])
        ev = _ncut.normalize(ev)

        mcut = np.inf
        threshold = None
        # Perform evenly spaced n-cuts and determine the optimal one.
        for t in np.linspace(0, 1, num_cuts, endpoint=False):
            mask = ev > t
            cost = _ncut.ncut_cost(mask, d, w)
            if cost < mcut:
                mcut = cost
                threshold = t

        if (mcut < thresh):
            mask = ev > threshold

            nodes1 = [n for i, n in enumerate(rag.nodes()) if mask[i]]
            nodes2 = [n for i, n in enumerate(rag.nodes()) if not mask[i]]

            # Sub divide and perform N-cut again
            sub1 = rag.subgraph(nodes1)
            sub2 = rag.subgraph(nodes2)

            _ncut_relabel(sub1, thresh, num_cuts)
            _ncut_relabel(sub2, thresh, num_cuts)
            return

    # Either an errornous condition occurred, or N-cut wasn't small enough.
    # The remaining graph is a region.
    # Assign `ncut label` by picking any label from the existing nodes, since
    # `labels` are unique, 'ncut label' is also unique.
    node = rag.nodes()[0]
    new_label = rag.node[node]['labels'][0]
    for n in rag.nodes():
        rag.node[n]['ncut label'] = new_label
