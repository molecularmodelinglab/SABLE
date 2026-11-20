"""Resolve enumeration breadth independently of downstream evaluation budgets."""
import os
from collections import Counter


def _integer(value, name, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f'{name} must be an integer between 1 and {maximum}')
    return value


def _setting(name, default, maximum):
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        raise ValueError(f'{name} must contain an integer') from None
    return _integer(value, name, maximum)


def enumeration_settings(inputs):
    """Keep the legacy raw-product alias while rejecting conflicting limits."""
    ceiling = _setting('SABLE_MAX_ENUMERATION_PRODUCTS', 20000, 100000)
    if 'max_total_products' in inputs and 'max_candidates' in inputs:
        raise ValueError('Supply max_total_products or its legacy alias max_candidates, not both')
    products = inputs.get('max_total_products', inputs.get('max_candidates'))
    if products is None:
        products = _setting('HEALER_MAX_TOTAL_PRODUCTS', 8000, ceiling)
    compositions = inputs.get('n_compositions')
    if compositions is None:
        compositions = _setting('HEALER_N_COMPOSITIONS', 100, 1000)
    blocks = inputs.get('max_bbs_per_frag')
    if blocks is None:
        blocks = _setting('HEALER_MAX_BBS_PER_FRAG', 10, 100)
    return {'max_total_products': _integer(products, 'max_total_products', ceiling),
            'n_compositions': _integer(compositions, 'n_compositions', 1000),
            'max_bbs_per_frag': _integer(blocks, 'max_bbs_per_frag', 100)}


def yield_summary(cap, source_count, considered_count, candidates, exclusions, backend_filtered_count=0):
    """Describe count reductions without promising an exact library yield."""
    return {'requested_product_cap': cap, 'source_count': source_count,
            'considered_count': considered_count, 'eligible_count': len(candidates),
            'excluded_count': len(exclusions), 'backend_filtered_count': backend_filtered_count,
            'truncated_count': max(0, source_count - backend_filtered_count - considered_count),
            'capacity_reached': source_count >= cap,
            'exclusion_counts': dict(Counter(row['reason'] for row in exclusions))}
