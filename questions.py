questions = {
    "general": [

    "Optimize sertraline for better properties.",
    "I need improved versions of metformin.",
    "Can you find better warfarin analogs with higher QED and good LogP?",
    "Starting from morphine, optimize for CNS penetration.",
    "Make ciprofloxacin more drug-like and lipophilic.",
    "Find analogs of sildenafil with better bioavailability metrics.",
    "Improve codeine for modern drug standards.",
    "Optimize this molecule: CC(=O)Oc1ccccc1C(=O)O for better ADME properties.",
    "Improve the solubility of Atorvastatin while maintaining its current drug-likeness score.",
    "Take molecule CC1=C(C=C(C=C1)NC(=O)C2=CC=C(C=C2)CN3CCN(CC3)C)NC4=NC=CC(=N4)C5=CN=CC=C5 and optimize it for blood-brain barrier penetration.",
    "I have a starting hit: Sildenafil. Generate analogs that reduce the molecular weight but keep the LogP above 2.0.",
    "Start with Omeprazole and optimize for minimum TPSA. I don't have specific iteration constraints, just run until convergence.",
    "Optimize Metoprolol for better metabolic stability. Focus on reducing the number of rotatable bonds.",
    "Find me a derivative of Fluoxetine with a higher QED. Feel free to explore the chemical space around it.",
    "Here is a fragment: c1ccccc1N. Grow this into a drug-like molecule with a QED > 0.6."
    "Starting from atorvastatin, optimize for high QED and low TPSA.",
    "I want to improve both the drug-likeness and lipophilicity of diazepam.",
    "Find captopril analogs that are more drug-like but also more permeable (lower TPSA).",
    "Optimize losartan for better QED and LogP between 2-4.",
    "Starting from tamoxifen, maximize drug-likeness while keeping molecular weight under 450 Da.",
    "Can you find methotrexate derivatives with improved bioavailability? I want higher LogP and lower polar surface area.",
    "Optimize fluoxetine for maximum QED and minimum number of rotatable bonds.",
    "Optimize Taxol for synthetic accessibility (SAScore). Enumerate 200 compounds. Run 25 iterations.",
    "Find ciprofloxacin analogs with better CNS penetration potential - higher LogP, lower TPSA, QED above 0.5.",
    "Starting from propranolol (CC(C)NCC(COC1=CC=CC=C1CC=C)O), optimize for QED > 0.7 and TPSA < 70 Ų. Enumerate 150 molecules and run 8 iterations.",
    "Optimize sertraline for maximum LogP (target > 4.5) while maintaining QED above 0.65. Generate 200 analogs, batch size 6, 10 iterations.",
    "Starting from omeprazole (CC1=CN=C(C(=C1OC)C)CS(=O)C2=NC3=C(N2)C=CC(=C3)OC), simultaneously optimize for QED > 0.8, molecular weight < 350 Da, and TPSA between 40-70 Ų. Enumerate 250 molecules, 12 iterations with batch size 8.",
    "Find improved analogs of amoxicillin with QED > 0.6, fewer than 3 hydrogen bond donors, and LogP between 0-2. Enumerate 100 molecules, optimize for 6 iterations.",
    "Optimize ranitidine derivatives for oral bioavailability: maximize QED, keep TPSA < 80 Ų, LogP between 1-3, and molecular weight < 400 Da. Generate 180 analogs, batch size 5.",
    "Starting from levothyroxine, create 300 derivatives and optimize for improved drug-likeness (QED > 0.75) and reduced molecular complexity (fewer rotatable bonds, target < 8). Run 15 iterations.",
    "Improve this failed drug candidate (high clearance, poor PK): ketoconazole. Optimize for reduced metabolism (lower LogP, reduce CYP interaction sites), maintained antifungal activity profile, QED > 0.7, MW < 500. Enumerate 200 molecules, 12 iterations.",
    "Starting from acyclovir (Nc1nc(=O)c2ncn(COCCO)c2[nH]1), design prodrug derivatives with improved oral absorption (higher LogP 1-3, balanced TPSA) that can convert to active form. Focus on QED and ADME properties. Enumerate 120 molecules, 8 iterations.",
    "Starting from diclofenac, find the best analog for QED in the minimum number of iterations. Enumerate 50 molecules, batch size 10.",
    "Starting from caffeine (CN1C=NC2=C1C(=O)N(C(=O)N2C)C), simultaneously optimize for maximum QED (drug-likeness), minimum TPSA (<60 Ų), and LogP between 2-4. Enumerate 200 analogs and run 10 BO iterations with batch size 8.",
    "Starting from aspirin, create the largest possible chemical space by enumerating 500 molecules, then use Bayesian optimization to find compounds with QED > 0.8 and LogP > 3.0. Run 15 iterations with batch size 10.",
    "Find improved analogs of ibuprofen with higher QED in the minimum number of iterations. Enumerate 100 molecules and optimize with batch size 3. The system should converge to QED > 0.7 within 5 iterations.",
    "Optimize morphine derivatives for maximum CNS penetration (high LogP, low TPSA) while maintaining drug-likeness (QED > 0.6). Target LogP > 4.0 and TPSA < 40 Ų. Enumerate 200 analogs, 12 iterations.",
        
    ],

    "admet": [

    "Optimize this CNS drug candidate for better brain penetration: fluoxetine. Maximize LogP (target 4-5), minimize TPSA (< 60 Ų), maintain QED > 0.7, MW 300-400. Enumerate 200 analogs, 10 iterations.",
    "Find analogs of furosemide with reduced renal clearance: lower TPSA, moderate LogP increase (1-2.5), maintained drug-likeness. Generate 150 molecules, 8 iterations, batch size 6.",
    "PPI inhibitor optimization: Starting from a peptide-like molecule CC(C)CC(NC(=O)C(CC(C)C)NC(=O)C(C)NC)C(=O)O, find drug-like analogs. Challenge: reduce MW below 600, improve QED > 0.6, balance LogP 2-4, TPSA < 120. Enumerate 100 molecules, 8 iterations.",
    "Optimize for reduced hERG liability: Starting from astemizole (known hERG binder), modify structure to reduce basic nitrogen centers and lower LogP (target 2-3) while maintaining antihistamine scaffold and QED > 0.7. Enumerate 180 molecules, 10 iterations.",
    "Starting with Metformin, optimize for improved membrane permeability. Target a LogP > 0.5 while keeping Molecular Weight under 200 Da. Run for 5 iterations."
    "Take the structure of Nicotine and enumerate 50 analogs. Use Bayesian optimization to maximize QED. I want to see if we can make it more 'drug-like' while keeping the pyridine ring."
    "Improve the solubility of Indomethacin. Generate analogs that minimize LogP to below 3.0 without exceeding a TPSA of 80. Use default settings for enumeration and batches."
    "Starting from Ethanol (small fragment), explode the search space to find drug-like molecules with MW between 300-500 and QED > 0.6. This is a purely exploratory run; enumerate 500 compounds."
    "Optimize Warfarin to remove potential toxicity warnings. Focus on minimizing the number of structural alerts (PAINS) and maximizing QED."
    "I need a version of Diphenhydramine (Benadryl) that is less likely to cross the Blood-Brain Barrier. Maximize TPSA to > 90 and minimize LogP. Enumerate 100 variants."

    ],

    "multi_objective": [

    "Starting from Paclitaxel (Taxol), try to reduce the Molecular Weight to < 600 Da (currently ~850) while maintaining a TPSA > 120. This is a difficult optimization; run 25 iterations with a batch size of 20."
    "Optimize Atorvastatin (Lipitor) for CNS penetration. This requires driving LogP up (>4) and TPSA down (<60), which contradicts its current statin profile. Enumerate 300 analogs."
    "Find analogs of Vancomycin (macrocycle) that improve oral bioavailability. Maximize QED and minimize Rotatable Bonds. This is a stress test for the enumerator's handling of large structures."
    "Start with Cisplatin. We want to explore organometallic analogs. Optimize for QED > 0.4 and LogP > -1.0. Note: Ensure the Platinum atom is preserved during enumeration."
    "Simultaneously optimize Omeprazole for three objectives: Maximize QED, Target LogP exactly at 3.0 (+/- 0.2), and Minimize TPSA. Enumerate 250 compounds.",
    "Start from Tamoxifen. We need to simultaneously Maximize QED, Minimize Toxicity (assume a score model exists), and keep LogP strictly between 3 and 4. Run 20 iterations with a batch size of 10.",
    "Optimize Doxorubicin for reduced cardiotoxicity (maximize safety score). Strict constraint: The Molecular Weight must NOT exceed 550 Da (current is ~543, so this is tight). Enumerate 300 analogs.",
    "Starting from Risperidone: Maximize CNS MPO score (multiparameter optimization). This requires optimizing LogP, TPSA, pKa, and HBD simultaneously. Run a long campaign: 30 iterations, batch size 5.",
    "Optimize Ciprofloxacin. Instead of raw potency, maximize Ligand Efficiency (Binding Affinity divided by Heavy Atom Count). Enumerate 150 molecules.",
    "Start with the highly lipophilic drug Amiodarone. The goal is to lower LogP to < 5.0 (for solubility) while ensuring TPSA remains < 80 (for permeability). Run 15 iterations."
    "Optimize the antimalarial chloroquine for modern drug standards: QED > 0.8, LogP between 2-4 for good absorption, TPSA < 65 Ų for membrane permeability, and molecular weight 300-450 Da. Enumerate 250 analogs, 10 iterations, batch size 7.",
    "Starting from the antibiotic tetracycline, find analogs with: (1) improved drug-likeness (QED > 0.7), (2) reduced molecular weight (< 400 Da), (3) maintained polarity (TPSA 60-90 Ų), and (4) moderate lipophilicity (LogP 1-3). Generate 200 molecules, 12 iterations.",
    "Optimize the chemotherapy agent doxorubicin for better pharmacokinetics: maximize QED, optimize LogP to 2-3 range, reduce TPSA below 120 Ų, minimize rotatable bonds (< 6), and keep MW under 500 Da. Enumerate 300 analogs, batch size 10, 15 iterations.",
    "Find derivatives of the immunosuppressant cyclosporine with improved oral availability: QED > 0.6, reduce MW by at least 100 Da from parent (target < 1100 Da), TPSA < 200 Ų, and maintain some lipophilicity (LogP > 3). This is a challenging macrocyclic structure. Enumerate 150 molecules, 8 iterations.",
    "Starting from insulin glargine analogs (peptide-like), optimize for: drug-likeness, reduced molecular weight, fewer hydrogen bond donors/acceptors, and appropriate LogP for subcutaneous delivery. This will test the system's ability to handle peptide-mimetic chemistry. Enumerate 100 molecules, 6 iterations, batch size 4.",
    "Large-scale optimization starting from multiple beta-lactam antibiotics (penicillin, cephalexin, cefuroxime). For each parent, enumerate 300 analogs. Combine into a single campaign optimizing for: QED > 0.75, bacterial cell wall penetration properties (TPSA 60-100, LogP 0-2), and MW 300-500 Da. Total 900 molecules, 20 iterations, batch size 15.",
    "Start with three kinase inhibitors (dasatinib, gefitinib, sorafenib). Optimize each for binding to their respective targets (BCR-ABL, EGFR, RAF) while also optimizing pan-kinase selectivity profile (moderate LogP 2-3, QED > 0.7). Enumerate 200 analogs per parent, total 600 molecules, 15 iterations, batch size 12.",
    "Starting from 5 small fragments (MW < 250), use enumeration mode to generate diverse analogs. Optimize the combined library for: drug-likeness (QED > 0.7), lead-like properties (MW 300-450, LogP 1-4, TPSA < 90, HBD < 5), and synthetic accessibility. Enumerate 500 molecules per fragment (2500 total), 25 iterations, batch size 20.",
    "Starting from benzene, enumerate 1000 diverse molecules and optimize for QED > 0.85 and LogP exactly 3.0 (±0.2). Run 20 iterations with batch size 15.",
    "Starting from metoprolol (CCOC(=O)c1ccc(OCC(O)CNC(C)C)cc1), optimize for 7 simultaneous objectives: maximize QED, LogP=2.5±0.5, TPSA 50-70, MW<350, HBD<3, HBA<6, RotBonds<8. Enumerate 200 molecules, 12 iterations.",
    "Process a high-throughput campaign starting from paracetamol. Enumerate 1000 molecules, characterize all properties (QED, LogP, TPSA, MW, HBD, HBA, RotBonds), and run 20 BO iterations with batch size 15.",
    
    ],

    "protein_specific": [
    
    "Starting from caffeine (CN1C=NC2=C1C(=O)N(C(=O)N2C)C), simultaneously optimize for maximum QED (drug-likeness), LogP between 2-4, and also strong binding to UniProt P12345. Enumerate 200 analogs and run 3 BO iterations with batch size 3.",
    "Starting from Gefitinib, optimize for binding affinity against EGFR (UniProt P00533). We want to minimize the predicted pIC50 (maximize potency) while keeping QED > 0.7. Run 20 BO iterations.",
    "Start with Imatinib. Optimize specifically for binding to the BCR-Abl T315I mutant (gatekeeper mutation). Enumerate 200 analogs and run 10 BO iterations.",
    "Optimize a starting pan-kinase inhibitor C1=CC=C(C=C1)NC2=NC=NC3=C2C=CN3. Maximize binding to BRAF while minimizing binding to EGFR. (Dual objective).",
    "Starting with the inhibitor Venetoclax, generate smaller analogs (MW < 800) that maintain high affinity for BCL-2. Use a large enumeration space of 500 molecules.",
    """Design dual-target inhibitors: Starting from methotrexate (dihydrofolate reductase inhibitor), optimize for binding to both DHFR (UniProt P00374) and thymidylate synthase (UniProt P04818). 
                                                 Also optimize QED > 0.7 and cancer-relevant ADME (moderate LogP 0-2). Enumerate 200 molecules, 12 iterations.""",
    """Polypharmacology optimization: Starting from aripiprazole, optimize for multi-target binding (D2 receptor UniProt P14416, 5-HT1A receptor UniProt P08908, 5-HT2A receptor UniProt P28223)
          while maintaining antipsychotic drug-like properties (QED > 0.7, LogP 3-5, CNS penetration). Generate 250 molecules, 10 iterations, batch size 8.""",
    "Starting from oseltamivir (CCOC(=O)C1C(CC(CC1NC(=O)C)N)C=C), optimize for maximum QED and strong binding to influenza neuraminidase (UniProt P03496). Enumerate 200 analogs, run 5 BO iterations with batch size 5.",
    "Find erlotinib derivatives with improved binding to EGFR kinase domain (UniProt P00533) while maintaining QED > 0.7 and optimizing ADME properties (LogP 2-4, TPSA < 80). Generate 250 molecules, 8 iterations, batch size 6.",
    "Optimize the BRAF inhibitor vemurafenib for: (1) stronger binding to BRAF V600E mutant (UniProt P15056), (2) QED > 0.75, (3) improved CNS exclusion (lower LogP, higher TPSA), and (4) MW < 450 Da. Enumerate 200 analogs, 10 iterations.",
    "Starting from the HIV protease inhibitor ritonavir, simultaneously optimize for binding to HIV-1 protease (UniProt P03367), improved drug-likeness (QED > 0.7), and better oral bioavailability (TPSA < 100, LogP 3-5, MW < 650). Generate 300 molecules, 12 iterations, batch size 8.",
    "Find analogs of the kinase inhibitor imatinib with improved binding to BCR-ABL (UniProt P00519), reduced off-target binding potential (lower lipophilicity, target LogP 2-3), maintained drug-likeness (QED > 0.75), and reduced molecular weight (< 450 Da). Enumerate 250 molecules, 10 iterations.",
    "I'm working on improving a p38 MAPK inhibitor for rheumatoid arthritis. Starting compound: c1ccc(cc1)C(=O)Nc2cccc(c2)C(F)(F)F. Need better drug-likeness, oral bioavailability (LogP 2-3.5, TPSA < 75), and maintained target binding (UniProt Q16539). Enumerate 200 analogs.",
    "We have a CDK2 inhibitor hit from virtual screening: Nc1nc(Nc2ccccc2)nc2[nH]cnc12. Use lead optimization to add R-groups. Optimize for: binding to CDK2 (UniProt P24941), selectivity over related kinases (moderate LogP ~2.5), QED > 0.75, MW < 400. Generate 180 variants, 10 iterations, batch size 6.",
    "Merge properties from two fragments - Fragment A (high binding affinity, poor ADME) and Fragment B (good ADME, weak binding). Starting SMILEs: c1ccccc1CN and CC(=O)Nc1ccccc1O. Optimize merged analogs for both binding to target (UniProt P00533) and drug-likeness. Generate 150 molecules, 8 iterations.",

    ],

    "lead_optimization": [

    "Perform lead optimization on Diazepam ClC1=CC=C2N(C)C(=O)CN=C2C1C5=CC=CC=C5. Keep the benzodiazepine core fixed and only enumerate R-groups at the N1 and C3 positions to maximize predicted binding to GABA-A.",
    "Starting with the penicillin core N12[C@@H](S[C@H](C1=O)C(C)(C)C(=O)O)NC(=O)C, enumerate side chains on the amine group. We need to increase LogP to > 1.5 without breaking the beta-lactam ring. Generate 50 analogs.",
    "Optimize the R-groups of Celecoxib. We need to lower the LogP below 3.5 while maximizing QED. Use a standard Suzuki coupling library for enumeration. Batch size 5, 10 iterations.",
    "Take the drug Losartan. Fix the biphenyl-tetrazole scaffold and vary the imidazole side chain to maximize H-bond donors. Enumerate 100 compounds."
    "I have a hit compound from HTS: c1ccc(cc1)C(=O)Nc2ccccc2. Use lead optimization mode to add R-groups and optimize for QED > 0.8 and LogP between 3-4. Keep the core scaffold fixed.",
    "Starting with the benzimidazole core Nc1nc2ccccc2[nH]1, enumerate R-group additions to optimize for maximum binding to protein kinase C (UniProt P05129) while maintaining QED > 0.7. Generate 150 derivatives, 8 iterations, batch size 5.",
    "Use lead optimization mode on this thienopyrimidine scaffold: c1cc2c(s1)ncnc2N. Optimize decorated versions for QED > 0.75, TPSA 50-80 Ų, and LogP 2-3.5. Enumerate 100 R-group variants.",
    "Fix the quinoline scaffold Oc1ccnc2ccccc12 and enumerate R-group substitutions. Optimize for strong binding to topoisomerase II (UniProt P11388), QED > 0.7, and molecular weight < 400 Da. Generate 200 variants, 10 iterations.",
    "Use the Penicillin beta-lactam core as a fixed scaffold. Enumerate R-groups at the acyl side chain to improve acid stability (target QED > 0.7 and low Rotatable Bonds). Generate 200 compounds.",
    "Starting with Imatinib (Gleevec), keep the phenylaminopyrimidine scaffold fixed. Optimize the terminal R-groups to reduce Molecular Weight below 450 Da while maintaining a QED > 0.6.",
    "Perform a Lead Opt campaign on the Benzodiazepine core. We want to explore 300 analogs. The objective is to find compounds with LogP between 2.5 and 3.5. Use a batch size of 10 for 15 iterations.",
    "Fix the steroid backbone of Dexamethasone. Enumerate diverse functional groups at the C-17 position to maximize H-bond donors. I don't care about iterations, just find the top 5 candidates.",
    "Using the Coumarin scaffold, generate a library of 150 compounds. Optimize for maximum QED and Synthetic Accessibility (SAS) < 4. Run 8 iterations.",
    
    ],

    "combination_therapy": [

    "Starting from methotrexate (dihydrofolate reductase inhibitor), optimize for binding to both DHFR (UniProt P00374) and thymidylate synthase (UniProt P04818). Also optimize QED > 0.7 and cancer-relevant ADME (moderate LogP 0-2). Enumerate 200 molecules, 12 iterations.",
    "Starting from aripiprazole, optimize for multi-target binding (D2 receptor UniProt P14416, 5-HT1A receptor UniProt P08908, 5-HT2A receptor UniProt P28223) while maintaining antipsychotic drug-like properties (QED > 0.7, LogP 3-5, CNS penetration). Generate 250 molecules, 10 iterations, batch size 8.",
    
    ],

    "natural_products": [

    "Optimize the complex natural product paclitaxel (taxol) for improved druggability. This molecule has: multiple stereocenters, high MW, high TPSA. Target: QED > 0.6, reduce MW below 800 Da, TPSA < 150 Ų, maintain core structure for bioactivity. Enumerate 100 analogs (this will be challenging), 8 iterations.",
    "Starting from the macrolide antibiotic erythromycin, find semi-synthetic derivatives with: improved acid stability, better oral bioavailability (LogP 2-4, TPSA 100-160), QED > 0.65, and maintained macrocyclic structure. Enumerate 150 molecules, 10 iterations, batch size 6.",
    "Optimize vancomycin (glycopeptide antibiotic) analogs for improved pharmacokinetics while maintaining the core binding mechanism. Target: reduced MW (< 1200 Da), improved membrane permeability (challenge for this scaffold), QED > 0.5. Enumerate 80 molecules, 6 iterations.",
    "Optimize resveratrol (C1=CC(=CC(=C1)O)C=CC2=CC(=CC(=C2)O)O) for improved stability and bioavailability. Target: QED > 0.7, LogP 2-4, maintained polyphenol activity. Enumerate 180 molecules, 10 iterations.",
    "Find curcumin derivatives with improved metabolic stability: reduce number of metabolically labile sites, increase LogP to 3-4, maintain QED > 0.65, TPSA < 100. Generate 150 analogs, 8 iterations.",
    "Optimize the flavonoid epicatechin for better oral absorption while maintaining antioxidant scaffold: increase LogP (target 2-3), reduce TPSA (< 80), QED > 0.7. Enumerate 120 molecules, 8 iterations, batch size 5.",
    "Optimize the natural product quercetin (C1=CC(=C(C=C1C2=C(C(=O)C3=C(C=C(C=C3O2)O)O)O)O)O) for improved bioavailability by maximizing QED and LogP while minimizing molecular weight below 400 Da. Generate 150 derivatives and optimize for 8 iterations",

    ],

    "edge_cases": [

    "Start with water (O). Optimize for QED.",
    "Optimize NotAMolecule for LogP.",
    "Start with Caffeine. Enumerate 10 molecules. Run 50 iterations with batch size 20.",
    "Start with Methane (C). Enumerate analogs. Optimize for QED."
    "Starting from Aspirin, create the largest possible chemical space your enumerator allows (up to 5000 molecules). Run a high-throughput optimization: 50 iterations, batch size 50. Objective: Maximize QED.",
    "I have a list of three different NSAIDs: Ibuprofen, Naproxen, and Diclofenac. Load all three. For each, generate 50 analogs. Run a single BO campaign that optimizes the average QED of the entire set. Total budget: 100 evaluations.",
    "Simultaneously optimize three different drug classes: start with aspirin (anti-inflammatory), metformin (antidiabetic), and warfarin (anticoagulant). For each, maximize QED and optimize class-specific properties. Combine all results into a single optimization campaign with 300 total molecules.",
    "Start with a simple benzene ring c1ccccc1. Use the enumerator to add fragments until the Molecular Weight is between 300 and 400. Then, use BO to optimize the resulting library for QED > 0.7. Iterations: 15.",
    "Optimize Fentanyl. Maximize potency, but throw an error or penalize heavily if the resulting molecule contains a toxicophore (e.g., nitro group or hydrazine). Enumerate 100 analogs.",
    "Start with atenolol, optimize for QED and LogP 2-3. After initial optimization, take top 3 compounds and re-enumerate around them for further optimization. Total 2 rounds, 150 molecules per round.",
    "Starting from the statin atorvastatin, enumerate 300 structurally diverse molecules (not just decorations). Optimize for QED > 0.75, LogP 3-4.5, and TPSA 60-100 Ų suitable for HMG-CoA reductase inhibition. 12 iterations, batch size 8.",
    "Optimize this SMILES: X[Z]c1cc(Y)c.",

    ]
}