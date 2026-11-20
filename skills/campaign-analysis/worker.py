"""Render reproducible figures using shared objective-progress utilities."""
import base64
from io import BytesIO
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt
from utils.plot_workflow import (compute_objective_progress, _morgan_fp_matrix,
    plot_prop_vs_iteration, plot_minmax_vs_iteration, plot_objective_progress,
    smiles_to_svg, PLOTLY_HTML_CONFIG, HTML_POST_SCRIPT)
import plotly.graph_objects as go


def plot(payload):
    rows, kind = payload['rows'], payload['chart']
    name, unit = payload['property'], payload['unit']
    values = np.array([row['value'] for row in rows], dtype=float)
    title = {'distribution': 'Property distribution', 'progress': 'Optimization progress', 'chemical_space': 'Chemical space', 'box': 'Values by optimization round', 'violin': 'Distributions by optimization round', 'minmax': 'Observed range by optimization round'}[kind]
    image_budget = 250_000
    records = []
    for index, row in enumerate(rows):
        image = (smiles_to_svg(row['smiles']) or '') if index < 100 and image_budget > 0 else ''
        if len(image.encode()) > image_budget:
            image = ''
        image_budget -= len(image.encode())
        records.append({'molecule_id': row['id'], 'smiles': row['smiles'], 'iteration': row['iteration'], name: row['value'], 'image_svg': image})
    frame = pd.DataFrame(records)
    interactive = None
    fig, ax = plt.subplots(figsize=(7, 4.5), constrained_layout=True)
    try:
        if kind in {'box', 'violin', 'minmax'}:
            directions = {name: payload['direction'].lower()}
            interactive = (plot_minmax_vs_iteration(frame, name, directions=directions) if kind == 'minmax'
                           else plot_prop_vs_iteration(frame, name, kind=kind, directions=directions))
            groups = sorted(frame['iteration'].unique())
            grouped = [frame.loc[frame['iteration'] == group, name].to_numpy() for group in groups]
            if kind == 'minmax':
                ax.plot(groups, [v.min() for v in grouped], marker='o', label='Minimum')
                ax.plot(groups, [v.max() for v in grouped], marker='o', label='Maximum'); ax.legend()
            elif kind == 'violin' and all(len(v) > 1 and np.ptp(v) > 0 for v in grouped):
                ax.violinplot(grouped, positions=groups, showmeans=True)
            else:
                ax.boxplot(grouped, positions=groups)
            points = [{'x': row['iteration'], 'y': row['value'], 'label': row['id']} for row in rows]
            x_label, y_label, style = 'Optimization round', f'{name} ({unit})', 'scatter'
            interactive.update_layout(xaxis_title=x_label, yaxis_title=y_label)
            note = 'Recorded values by scientific optimization round; no unmeasured global optimum is assumed.'
        elif kind == 'progress':
            frame = pd.DataFrame([{'iteration': row['iteration'], name: row['value']} for row in rows])
            progress = compute_objective_progress(frame, None, name, payload['direction'].lower())
            interactive = plot_objective_progress(progress, name, payload['direction'].lower())
            interactive.update_yaxes(title_text=f'{name} ({unit})', row=1, col=1)
            interactive.update_xaxes(title_text='Optimization round', row=3, col=1)
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
        if interactive is None:
            if kind == 'distribution':
                interactive = go.Figure(go.Histogram(x=values, nbinsx=min(20, max(1, int(np.sqrt(len(values)))))))
            else:
                custom = [{'id': row['id'], 'smiles': row['smiles'], 'iter': row['iteration'], 'img': frame.iloc[i]['image_svg'], 'props': {name: row['value']}} for i, row in enumerate(rows)]
                interactive = go.Figure(go.Scatter(x=[p['x'] for p in points], y=[p['y'] for p in points], mode='markers',
                    marker={'color': values, 'colorscale': 'Viridis', 'showscale': True, 'colorbar': {'title': f'{name} ({unit})'}},
                    text=[row['id'] for row in rows], customdata=[[p] for p in custom], hovertemplate='%{text}<extra></extra>'))
            interactive.update_layout(title=title, xaxis_title=x_label, yaxis_title=y_label)
        interactive.update_layout(template='plotly_white', autosize=True)
        html = interactive.to_html(include_plotlyjs='cdn', full_html=True, config={**PLOTLY_HTML_CONFIG, 'responsive': True}, post_script=HTML_POST_SCRIPT)
        html = html.replace('<head>', '<head><meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; script-src https://cdn.plot.ly &#39;unsafe-inline&#39;; style-src &#39;unsafe-inline&#39;; img-src data: blob:;">', 1)
        ax.set(xlabel=x_label, ylabel=y_label, title=title)
        buffer = BytesIO()
        fig.savefig(buffer, format='png', dpi=160, metadata={'Software': 'SABLE'})
        svg = BytesIO()
        fig.savefig(svg, format='svg', metadata={'Date': None})
        summary = {'count':len(rows), 'minimum':float(values.min()), 'maximum':float(values.max()), 'mean':float(values.mean()), 'unit':unit}
        table = {'type':'table','title':'Source observations','columns':[{'key':k,'label':v} for k,v in [('id','Molecule'),('smiles','SMILES'),('value',f'{name} ({unit})')]],'rows':[{k:row[k] for k in ('id','smiles','value')} for row in rows]}
        return {'summary':summary, 'figure_svg': svg.getvalue().decode(), 'figure_html': html, 'figure_png_base64':base64.b64encode(buffer.getvalue()).decode(),
                'presentation':{'version':1,'title':title,'summary':note,'blocks':[
                    {'type':'chart','title':title,'style':style,'x_label':x_label,'y_label':y_label,'points':points},table]}}
    finally:
        plt.close(fig)
