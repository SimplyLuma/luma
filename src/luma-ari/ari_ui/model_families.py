# SPDX-License-Identifier: Apache-2.0
"""Family descriptions from the owner's October 29 Models design.

Downloadable sizes always come from Ari's pinned runtime catalogue. These
descriptions do not authorize downloads or invent an installed model.
"""
FAMILIES = {
    'Qwen3': dict(by='Qwen', line='An all-rounder for chat, writing and code', can=['chat', 'code', 'think'],
                  context=32768, licence='Apache 2.0', languages='119 languages and dialects', released='April 2025'),
    'Gemma 3': dict(by='Google', line='Clear writing, and it can read pictures', can=['chat', 'pictures'],
                    context=131072, licence='Gemma Terms of Use', languages='Over 140 languages', released='March 2025'),
    'DeepSeek R1': dict(by='DeepSeek', line='Works hard problems through before it answers', can=['think'],
                       context=131072, licence='MIT', released='January 2025, updated May 2025'),
    'Qwen3 Coder': dict(by='Qwen', line='Made for code. Reads a whole project, then writes and fixes', can=['code'],
                       context=262144, licence='Apache 2.0', released='July 2025'),
    'Qwen2.5 VL': dict(by='Qwen', line='Reads screenshots, photos, receipts and scans', can=['pictures', 'chat'],
                       context=128000, licence='Apache 2.0', released='January 2025'),
    'Mistral Small 3.2': dict(by='Mistral', line='Careful, clear writing, and it can read pictures', can=['chat', 'code', 'pictures'],
                            context=131072, licence='Apache 2.0', released='June 2025'),
    'Phi-4 mini': dict(by='Microsoft', line='Small and quick. Good for older computers', can=['chat'],
                       context=131072, licence='MIT', released='February 2025'),
    'Llama 3.3': dict(by='Meta', line='A large model for computers with a lot of memory', can=['chat', 'code'],
                      context=131072, licence='Llama 3.3 Community License', languages='8 languages', released='December 2024'),
}


def family_for(model):
    if model.get('family'):
        return model['family']
    name = model.get('n', '')
    for family in sorted(FAMILIES, key=len, reverse=True):
        if name.startswith(family):
            return family
    if name.startswith('DeepSeek R1'):
        return 'DeepSeek R1'
    return name


def families(models):
    result = {}
    for model in models:
        family = family_for(model)
        result.setdefault(family, []).append(model)
    order = {name: index for index, name in enumerate(FAMILIES)}
    return dict(sorted(result.items(), key=lambda item: order.get(item[0], len(order))))
