#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Admission checks for refreshing an already approved OS graph."""
BASELINE = '7105b1804d8948e661b651542be60fc7cc1a5c626673cab08f5404210a521df1'

def policy(graph):
    return {key: value for key, value in graph.items() if key != 'generated_at'}

def same_policy(approved, served):
    if policy(approved) != policy(served):
        raise ValueError('The published policy changed; admit its new approved refresh state before replacing it')

def admitted_release(release, manifest, delivery=None, gate=None, public_repository_url=None, provenance=None):
    if (release['commit'], release['version'], release['released_at']) != (
            manifest['commit'], manifest['version'], manifest['published_utc']):
        raise ValueError('Approved graph and genuine publication manifest disagree')
    if manifest.get('bootstrap') is not None:
        if (release['commit'] != BASELINE or release['version'] != '1.0.0-beta.1'
                or release['paused'] is not True or manifest['bootstrap'].get('paused') is not True):
            raise ValueError('The initial installation baseline must remain paused')
        return
    # Normal publication binds provenance by hash; source cleanliness lives
    # in that document, rather than in the release manifest itself.
    dirty = manifest.get('source_dirty')
    if provenance is not None:
        source = provenance.get('source', {})
        if source.get('revision') != manifest.get('source_revision'):
            raise ValueError('Published source revision and provenance disagree')
        if dirty is not None and dirty != source.get('dirty'):
            raise ValueError('Published source cleanliness and provenance disagree')
        dirty = source.get('dirty')
    if dirty is not False or manifest.get('gate', {}).get('result') != 'pass':
        raise ValueError('Only a clean, gate-qualified published release can be refreshed')
    if not gate or gate.get('commit') != release['commit'] or gate.get('result') != 'pass':
        raise ValueError('The release lacks its genuine passing gate evidence')
    if any(gate.get(stage) != 'pass' for stage in ('fresh', 'no_account')):
        raise ValueError('Fresh and no-account release gates are required')
    for stage in ('update', 'rollback'):
        accepted = ('pass',) if manifest.get('parent') else ('pass', 'not-applicable')
        if gate.get(stage) not in accepted:
            raise ValueError('Upgrade and rollback release gates are required')
    if (not delivery or delivery.get('schema') != 'org.projectluma.os-public-delivery/v1'
            or delivery.get('commit') != release['commit']
            or any(delivery.get(field) is not True for field in (
                'complete_closure_readback', 'commit_signature_verified', 'summary_signature_verified'))):
        raise ValueError('Release payload delivery and signatures are not proved')
    if public_repository_url is not None and delivery.get('repository_url') != public_repository_url:
        raise ValueError('Delivery evidence must bind the actual all-user repository URL')
