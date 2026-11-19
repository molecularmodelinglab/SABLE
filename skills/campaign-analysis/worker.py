"""Render reproducible figures using shared objective-progress utilities."""
import base64
from io import BytesIO
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt
from utils.plot_workflow import compute_objective_progress, _morgan_fp_matrix


def plot(payload):
    rows, kind = payload['rows'], payload['chart']
    name, unit = payload['property'], payload['unit']
    values = np.array([row['value'] for row in rows], dtype=float)
    title = {'distribution': 'Property distribution', 'progress': 'Optimization progress', 'chemical_space': 'Chemical space'}[kind]
    fig, ax = plt.subplots(figsize=(7, 4.5), constrained_layout=True)
    try:
        if kind == 'progress':
            frame = pd.DataFrame([{'iteration': row['iteration'], name: row['value']} for row in rows])
            progress = compute_objective_progress(frame, None, name, payload['direction'].lower())
            points = [{'x': int(row.iteration), 'y': float(row.best_so_far)} for row in progress.itertuples()]
            x_label, y_label, style = 'Optimization round', f'Best observed {name} ({unit})', 'line'
            ax.plot([p['x'] for p in points], [p['y'] for p in points], marker='o')
            note = 'Best observed value by scientific round; not model decisions.'
        elif kind == 'chemical_space':
            if len(rows) < 3:
                raise ValueError('Chemical-space projection requires at least three molecules')
            matrix = _morgan_fp_matrix([row['smiles'] for row in rows]).astype(float)
            matrix -= matrix.mean(axis=0)
            from sklearn.decomposition import PCA
            coords = PCA(n_components=2, svd_solver='full').fit_transform(matrix)
            points = [{'x': float(x), 'y': float(y), 'label': row['id']} for row,(x,y) in zip(rows,coords)]
            x_label, y_label, style = 'Fingerprint PC1', 'Fingerprint PC2', 'scatter'
            ax.scatter(coords[:, 0], coords[:, 1])
            note = 'PCA of Morgan fingerprints (radius 2, 2048 bits). Proximity is a projection of structural similarity, not biological activity.'
        else:
            points = [{'x': float(value), 'y': 1.0} for value in values]
            x_label, y_label, style = f'{name} ({unit})', 'Molecule count', 'distribution'
            ax.hist(values, bins=min(20, max(1, int(np.sqrt(len(values))))))
            note = 'Distribution of recorded computed values; not experimental measurements.'
        ax.set(xlabel=x_label, ylabel=y_label, title=title)
        buffer = BytesIO()
        fig.savefig(buffer, format='png', dpi=160, metadata={'Software': 'SABLE'})
        summary = {'count':len(rows), 'minimum':float(values.min()), 'maximum':float(values.max()), 'mean':float(values.mean()), 'unit':unit}
        table = {'type':'table','title':'Source observations','columns':[{'key':k,'label':v} for k,v in [('id','Molecule'),('smiles','SMILES'),('value',f'{name} ({unit})')]],'rows':[{k:row[k] for k in ('id','smiles','value')} for row in rows]}
        return {'summary':summary, 'figure_png_base64':base64.b64encode(buffer.getvalue()).decode(),
                'presentation':{'version':1,'title':title,'summary':note,'blocks':[
                    {'type':'chart','title':title,'style':style,'x_label':x_label,'y_label':y_label,'points':points},table]}}
    finally:
        plt.close(fig)
