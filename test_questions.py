questions = [
    "Starting from caffeine (CN1C=NC2=C1C(=O)N(C(=O)N2C)C), simultaneously optimize for maximum QED (drug-likeness), minimum TPSA (<60 Ų), and LogP between 2-4. Enumerate 200 analogs and run 10 BO iterations with batch size 8.",
    "Optimize the natural product quercetin (C1=CC(=C(C=C1C2=C(C(=O)C3=C(C=C(C=C3O2)O)O)O)O)O) for improved bioavailability by maximizing QED and LogP while minimizing molecular weight below 400 Da. Generate 150 derivatives and optimize for 8 iterations",
    "Starting from aspirin, create the largest possible chemical space by enumerating 500 molecules, then use Bayesian optimization to find compounds with QED > 0.8 and LogP > 3.0. Run 15 iterations with batch size 10.",
    "Find improved analogs of ibuprofen with higher QED in the minimum number of iterations. Enumerate 100 molecules and optimize with batch size 3. The system should converge to QED > 0.7 within 5 iterations.",
    "Optimize morphine derivatives for maximum CNS penetration (high LogP, low TPSA) while maintaining drug-likeness (QED > 0.6). Target LogP > 4.0 and TPSA < 40 Ų. Enumerate 200 analogs, 12 iterations.",
    "Simultaneously optimize three different drug classes: start with aspirin (anti-inflammatory), metformin (antidiabetic), and warfarin (anticoagulant). For each, maximize QED and optimize class-specific properties. Combine all results into a single optimization campaign with 300 total molecules.",
    "Process a high-throughput campaign starting from paracetamol. Enumerate 1000 molecules, characterize all properties (QED, LogP, TPSA, MW, HBD, HBA, RotBonds), and run 20 BO iterations with batch size 15. Include checkpoint saves every 3 iterations.",
    "Optimize challenging molecular scaffolds including: macrocycles, organometallics, and highly strained rings. Start with cyclophane and optimize for synthetic accessibility while maintaining QED > 0.5. Enumerate 100 analogs.",
    

]