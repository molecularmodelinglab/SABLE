"""Queue molecular property calculation without contacting the service."""


def characterize(inputs, context):
    molecules = inputs['molecules']
    if len({row['id'] for row in molecules}) != len(molecules):
        raise ValueError('Molecule IDs must be unique')
    return context.submit_job('properties', inputs)
