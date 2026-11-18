from statistics import mean


def summarize(inputs, context):
    values = inputs['values']
    return dict(count=len(values), minimum=min(values), maximum=max(values),
                mean=mean(values), unit=inputs['unit'])
