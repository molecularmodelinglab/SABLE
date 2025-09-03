import streamlit as st
import json
import pandas as pd
from datetime import datetime
from typing import Dict,Any
import plotly.express as px
import plotly.graph_objects as go
from rdkit import Chem
from rdkit.Chem import Draw


from orchestrate import GeminiToolOrchestrator, BAYBE_AVAILABLE
from tools.enumerator_tool import EnumeratorTool
from tools.bayesopt_tool import BayesianOptimizationTool
from tools.stoplight_tool import StoplightTool
from tools.molecule_characterization_tool import MoleculeCharacterizationTool
from tools.measurement_extractor_tool import MeasurementExtractor
from tools.report_generator_tool import ReportGenerator
from tools.workflow_summary_tool import WorkflowSummary

class WorkflowTemplate:
    """Template for common discovery workflows"""
    def __init__(self, name: str, description: str, prompt_template: str, parameters: Dict[str, Any]):
        self.name = name
        self.description = description
        self.prompt_template = prompt_template
        self.parameters = parameters

class ChemistryDiscoveryInterface:
    def __init__(self):
        self.orchestrator = None
        self.workflow_history = []
        self.templates = self._create_workflow_templates()
        
    def _create_workflow_templates(self) -> Dict[str, WorkflowTemplate]:
        """Create predefined workflow templates"""
        templates = {}
        
        # Lead Optimization Template
        templates["lead_optimization"] = WorkflowTemplate(
            name="Lead Optimization",
            description="Optimize a known active compound for better properties",
            prompt_template="""
I need to optimize a lead compound for drug discovery. Starting molecule: {starting_molecule}

Goals:
- Generate {n_variants} similar molecules using enumeration
- Optimize for: {objectives}
- Find top {batch_size} candidates using Bayesian optimization
- Characterize properties of final recommendations

Please follow this workflow:
1. Use Enumerator to generate {n_variants} similar molecules
2. Use BayesianOptimizer for initial {batch_size} recommendations (no measurement data)
3. Characterize the recommended molecules with MoleculeCharacterizer
4. Extract measurements and run BO again with feedback
5. Generate final report with ReportGenerator
6. Create workflow summary with WorkflowSummary
            """,
            parameters={
                "starting_molecule": "CC(=O)OC1=CC=CC=C1C(=O)O",
                "n_variants": 20,
                "objectives": ["QED"],
                "batch_size": 5
            }
        )
        
        # Property Optimization Template
        templates["property_optimization"] = WorkflowTemplate(
            name="Multi-Property Optimization",
            description="Optimize multiple molecular properties simultaneously",
            prompt_template="""
I want to optimize multiple properties for drug discovery. Starting molecule: {starting_molecule}

Multi-objective optimization for:
{objectives}

Workflow:
1. Generate {n_variants} diverse molecules with Enumerator
2. Set up multi-objective Bayesian optimization for {objectives}
3. Run iterative optimization cycles
4. Characterize top candidates
5. Generate comprehensive analysis report
            """,
            parameters={
                "starting_molecule": "c1ccccc1",
                "n_variants": 30,
                "objectives": ["QED", "SlogP", "MW"],
                "batch_size": 8
            }
        )
        
        # Scaffold Hopping Template
        templates["scaffold_hopping"] = WorkflowTemplate(
            name="Scaffold Hopping",
            description="Find diverse chemical scaffolds with similar activity",
            prompt_template="""
I need to find diverse chemical scaffolds while maintaining activity. Starting from: {starting_molecule}

Focus on:
- Structural diversity (low similarity threshold: {similarity_threshold})
- Maintaining {key_properties}
- Exploring {reaction_types} chemistry

Process:
1. Generate diverse molecules with custom reaction tags: {reaction_types}
2. Use low similarity threshold for diversity
3. Optimize for {key_properties}
4. Analyze scaffold diversity in results
            """,
            parameters={
                "starting_molecule": "CCN1CCN(CC1)c2ccc(Cl)cc2",
                "similarity_threshold": 0.3,
                "key_properties": ["QED", "Activity"],
                "reaction_types": ["C-N bond formation", "alkylation", "amination"]
            }
        )
        
        return templates

def create_streamlit_interface():
    """Create the main Streamlit interface"""
    st.set_page_config(
        page_title="Chemistry Discovery Assistant",
        page_icon="🧪",
        layout="wide"
    )
    
    st.title("🧪 Drug Discovery Assistant")
    st.markdown("*AI-powered molecular discovery and optimization*")
    
    # Initialize interface
    if 'interface' not in st.session_state:
        st.session_state.interface = ChemistryDiscoveryInterface()
    
    interface = st.session_state.interface
    
    # Sidebar for workflow selection
    with st.sidebar:
        st.header("Workflow Selection")
        
        workflow_type = st.selectbox(
            "Choose a workflow:",
            ["Custom", "Lead Optimization", "Multi-Property Optimization", "Scaffold Hopping"]
        )
        
        st.header("Quick Actions")
        if st.button("🔄 Reset Session"):
            st.session_state.clear()
            st.rerun()
        
        if st.button("📋 View Memory"):
            if hasattr(interface, 'orchestrator') and interface.orchestrator:
                st.json(interface.orchestrator.memory)
    
    # Main interface
    if workflow_type == "Custom":
        create_custom_workflow_interface(interface)
    else:
        create_template_workflow_interface(interface, workflow_type.lower().replace("-", "_").replace(" ", "_"))

def create_custom_workflow_interface(interface):
    """Create interface for custom workflows"""
    st.header("Custom Workflow")
    
    col1, col2 = st.columns([2, 1])
    
    with col1:
        st.subheader("Workflow Configuration")
        
        # Starting molecule input
        starting_molecule = st.text_input(
            "Starting Molecule (SMILES)",
            value="CC(=O)OC1=CC=CC=C1C(=O)O",
            help="Enter a SMILES string for your starting molecule"
        )
        
        # Validate SMILES
        if starting_molecule:
            mol = Chem.MolFromSmiles(starting_molecule)
            if mol:
                st.success("✅ Valid SMILES")
                # Show molecule structure
                img = Draw.MolToImage(mol, size=(300, 200))
                st.image(img, caption="Starting Molecule Structure")
            else:
                st.error("❌ Invalid SMILES string")
        
        # Objective selection
        objectives = st.multiselect(
            "Optimization Objectives",
            ["QED", "SlogP", "MW", "TPSA", "HBA", "HBD", "RotBonds"],
            default=["QED"]
        )
        
        # Advanced parameters
        with st.expander("Advanced Parameters"):
            n_variants = st.slider("Number of variants to generate", 10, 100, 20)
            batch_size = st.slider("Batch size for recommendations", 3, 20, 5)
            similarity_threshold = st.slider("Similarity threshold", 0.1, 0.9, 0.5)
            
            building_blocks = st.selectbox(
                "Building blocks source",
                ["test", "EU_stock", "US_stock", "Global_stock"]
            )
        
        # Custom prompt
        st.subheader("Custom Workflow Prompt")
        custom_prompt = st.text_area(
            "Edit the workflow prompt:",
            value=f"""I need to find new drug candidates. Please start with the molecule Aspirin, which has the SMILES '{starting_molecule}'.

Goals: Optimize for {', '.join(objectives)}

Please:
1. Use the Enumerator tool to generate a library of {n_variants} molecules similar to {starting_molecule}
2. Use the BayesianOptimizer tool to get an initial batch of {batch_size} recommended molecules to test from the generated library. Do not provide any measurement data for this first run. Use MORDRED descriptors for the optimization.
3. For each of the 5 recommended molecules, use the MoleculeCharacterizer tool to calculate their properties. To reference the recommendations from step 2, use "bo_recommendations" as the molecule_ids parameter.
4. Use the MeasurementExtractor tool to extract the {', '.join(objectives)} values from the characterization results and format them as measurement data for the next optimization step. IMPORTANT: Use "Molecule_ID" as the id_column_name to match the BayesianOptimizer's expectations.
5. Run the BayesianOptimizer tool a second time. This time, provide the measurement data you just extracted for {', '.join(objectives)} to get improved recommendations. Use "measurement_data" as the measurement_data parameter.
6. IMPORTANT: Use the MoleculeCharacterizer tool again to characterize the NEW set of recommended molecules from step 5. Use "bo_recommendations" as the molecule_ids parameter to get the final set of recommendations with their properties.
7. Use the ReportGenerator tool to create a final summary report of the recommended molecules and their properties. The report should prominently display the {', '.join(objectives)} scores for each recommended molecule.
8. Use the WorkflowSummary tool to generate a comprehensive summary showing all evaluated molecules, both optimization rounds, and the improvement analysis.
Note: When referencing data stored in memory, use the simple key name (e.g., "bo_recommendations", "measurement_data") without any special syntax.

Use a batch size of {batch_size} for the BayesianOptimizer tool.
Similarity threshold: {similarity_threshold}""",
            height=300
        )
    
    with col2:
        st.subheader("Execution")
        
        if st.button("🚀 Run Workflow", type="primary"):
            if starting_molecule and objectives:
                run_workflow(interface, custom_prompt, "Custom Workflow")
            else:
                st.error("Please provide starting molecule and objectives")
        
        # Tool status
        st.subheader("Available Tools")
        tool_status = {
            "Enumerator": "✅ Ready",
            "BayesianOptimizer": "✅ Ready" if BAYBE_AVAILABLE else "❌ BayBE not installed",
            "MoleculeCharacterizer": "✅ Ready",
            "ReportGenerator": "✅ Ready"
        }
        
        for tool, status in tool_status.items():
            st.markdown(f"**{tool}**: {status}")

def create_template_workflow_interface(interface, template_key):
    """Create interface for template workflows"""
    if template_key not in interface.templates:
        st.error(f"Template {template_key} not found")
        return
    
    template = interface.templates[template_key]
    
    st.header(template.name)
    st.markdown(template.description)
    
    col1, col2 = st.columns([2, 1])
    
    with col1:
        st.subheader("Parameters")
        
        # Create input fields for template parameters
        params = {}
        for param, default_value in template.parameters.items():
            if param == "starting_molecule":
                params[param] = st.text_input(
                    "Starting Molecule (SMILES)",
                    value=default_value
                )
            elif param == "objectives":
                if isinstance(default_value, list):
                    params[param] = st.multiselect(
                        "Optimization Objectives",
                        ["QED", "SlogP", "MW", "TPSA", "HBA", "HBD", "RotBonds"],
                        default=default_value
                    )
            elif isinstance(default_value, int):
                params[param] = st.number_input(
                    param.replace("_", " ").title(),
                    value=default_value
                )
            elif isinstance(default_value, float):
                params[param] = st.slider(
                    param.replace("_", " ").title(),
                    0.0, 1.0, default_value
                )
            else:
                params[param] = st.text_input(
                    param.replace("_", " ").title(),
                    value=str(default_value)
                )
        
        # Show generated prompt
        st.subheader("Generated Workflow Prompt")
        formatted_prompt = template.prompt_template.format(**params)
        st.text_area("Prompt:", value=formatted_prompt, height=200, disabled=True)
    
    with col2:
        st.subheader("Execution")
        
        if st.button("🚀 Run Template", type="primary"):
            formatted_prompt = template.prompt_template.format(**params)
            run_workflow(interface, formatted_prompt, template.name)

def run_workflow(interface, prompt, workflow_name):
    """Execute a workflow and display results"""
    with st.spinner(f"Running {workflow_name}..."):
        try:
            # Initialize orchestrator if needed
            if not interface.orchestrator:
                available_tools = [
                    EnumeratorTool(),
                    StoplightTool(),
                    MoleculeCharacterizationTool(),
                    MeasurementExtractor(),
                    ReportGenerator(),
                    WorkflowSummary(),
                ]
                
                if BAYBE_AVAILABLE:
                    available_tools.append(BayesianOptimizationTool())
                
                interface.orchestrator = GeminiToolOrchestrator(tools=available_tools)
            
            # Run the workflow
            interface.orchestrator.run_conversation(prompt)
            
            # Store in history
            interface.workflow_history.append({
                "name": workflow_name,
                "timestamp": datetime.now(),
                "prompt": prompt,
                "memory": interface.orchestrator.memory.copy()
            })
            
            st.success("✅ Workflow completed!")
            
            # Display results
            display_workflow_results(interface.orchestrator.memory)
            
        except Exception as e:
            st.error(f"❌ Workflow failed: {str(e)}")

def display_workflow_results(memory):
    """Display workflow results in organized tabs"""
    if not memory:
        st.info("No results to display")
        return
    
    tab1, tab2, tab3, tab4 = st.tabs(["📊 Summary", "🧪 Molecules", "📈 Properties", "📋 Raw Data"])
    
    with tab1:
        st.subheader("Workflow Summary")
        if 'workflow_summary' in memory:
            st.text(memory['workflow_summary'])
        elif 'final_report' in memory:
            st.markdown(memory['final_report'])
        else:
            st.info("No summary available")
    
    with tab2:
        st.subheader("Recommended Molecules")
        if 'bo_recommendations' in memory and 'enumerated_molecules' in memory:
            display_molecule_recommendations(memory)
        else:
            st.info("No molecule recommendations available")
    
    with tab3:
        st.subheader("Molecular Properties")
        if 'characterization_results' in memory:
            display_property_analysis(memory['characterization_results'])
        else:
            st.info("No property data available")
    
    with tab4:
        st.subheader("Raw Memory Data")
        st.json(memory)

def display_molecule_recommendations(memory):
    """Display molecule recommendations with structures"""
    recommendations = memory.get('bo_recommendations', [])
    all_molecules = memory.get('enumerated_molecules', {})
    char_results = memory.get('characterization_results', {})
    
    if not recommendations:
        st.info("No recommendations found")
        return
    
    # Create a DataFrame for display
    data = []
    for rec_id in recommendations:
        smiles = all_molecules.get(rec_id, "Unknown")
        properties = char_results.get(rec_id, {})
        
        row = {"ID": rec_id, "SMILES": smiles}
        row.update(properties)
        data.append(row)
    
    df = pd.DataFrame(data)
    
    # Display table
    st.dataframe(df, use_container_width=True)
    
    # Display molecular structures
    st.subheader("Molecular Structures")
    cols = st.columns(min(3, len(recommendations)))
    
    for i, rec_id in enumerate(recommendations[:6]):  # Show max 6 structures
        col_idx = i % 3
        with cols[col_idx]:
            smiles = all_molecules.get(rec_id, "")
            if smiles and smiles != "Unknown":
                mol = Chem.MolFromSmiles(smiles)
                if mol:
                    img = Draw.MolToImage(mol, size=(250, 200))
                    st.image(img, caption=f"ID: {rec_id}")
                    st.code(smiles, language=None)

def display_property_analysis(char_results):
    """Display property analysis with visualizations"""
    if not char_results:
        st.info("No characterization data available")
        return
    
    # Convert to DataFrame
    df_data = []
    for mol_id, properties in char_results.items():
        row = {"Molecule_ID": mol_id}
        row.update(properties)
        df_data.append(row)
    
    df = pd.DataFrame(df_data)
    
    # Property distribution plots
    numeric_cols = df.select_dtypes(include=['float64', 'int64']).columns
    
    if len(numeric_cols) > 0:
        st.subheader("Property Distributions")
        
        # Select properties to plot
        selected_props = st.multiselect(
            "Select properties to visualize:",
            list(numeric_cols),
            default=list(numeric_cols)[:3]
        )
        
        if selected_props:
            fig = go.Figure()
            
            for prop in selected_props:
                fig.add_trace(go.Box(
                    y=df[prop],
                    name=prop,
                    boxpoints='all',
                    jitter=0.3,
                    pointpos=-1.8
                ))
            
            fig.update_layout(
                title="Property Distribution Comparison",
                yaxis_title="Property Value",
                showlegend=True
            )
            
            st.plotly_chart(fig, use_container_width=True)
        
        # Property correlation
        if len(selected_props) > 1:
            st.subheader("Property Correlations")
            corr_matrix = df[selected_props].corr()
            
            fig = px.imshow(
                corr_matrix,
                text_auto=True,
                aspect="auto",
                title="Property Correlation Matrix"
            )
            st.plotly_chart(fig, use_container_width=True)
    
    st.subheader("Detailed Property Data")
    st.dataframe(df, use_container_width=True)

if __name__ == "__main__":
    create_streamlit_interface()