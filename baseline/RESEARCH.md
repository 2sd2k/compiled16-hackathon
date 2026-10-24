# Research informs policy, not fabricated song labels

This module predicts DEAM perceived arousal and valence. Papers do not supply
per-song treatment labels, patient responses, or HR training pairs. No paper text
is fed into the regressor. The repository's physiological-response product brief
remains a separate future experiment requiring real paired recordings.

| Source | Supported interpretation | Implementation constraint |
| --- | --- | --- |
| [van der Steen et al., Cochrane, 2025](https://doi.org/10.1002/14651858.CD003477.pub5) | Evidence concerns structured music interventions for people with dementia; individual preferences are among intervention criteria. Effects and certainty vary by outcome and comparison. | Explicit preference ranks ahead of predicted affect; exclusions and discomfort override ranking. Do not claim an affect predictor reproduces a therapeutic intervention. |
| [Bernardi, Porta & Sleight, Heart, 2006](https://pubmed.ncbi.nlm.nih.gov/16199412/) | A small laboratory study of 24 adults linked tempo and pauses with cardiorespiratory changes. It does not establish patient-specific treatment efficacy. | Lower arousal is a configurable settling hypothesis; preserve pauses and a person-specific reference. No causal HR claim. |
| [Calamassi & Pomponi, Explore, 2019](https://www.sciencedirect.com/science/article/pii/S1550830718302763) | A pilot compared tuning standards. A small tuning comparison does not establish a universal healing frequency or generalize to dementia care. | No 432/528 Hz preference, frequency prescription, or healing-frequency feature/label is introduced. |

Low arousal and positive valence are **engineering priors for a settling demo**,
not universal clinical recommendations. Targets 3.0 and 6.5, preference ordering,
distance formula, and the simulated HR hook's 10 BPM boundary are unvalidated
policy parameters. Positive valence is a design choice; these sources do not
validate that exact quadrant, numerical thresholds, or individual song effects.
A patient's preferred music can differ from this default and ranks first.

The stateless HR hook accepts synthetic values only by contract and refuses
`simulated=False`. It outputs stop/pause/hold or ranking parameters, never a
predicted HR reduction. It cannot verify whether callers falsely mark real data
as simulated. It has no device connection, actual playback, medical thresholds,
persistent adaptation, or deployed patient control. Stop and discomfort take
priority, followed by care presence and signal validity. No synthetic HR outcomes
are used in training or evaluation.

Before clinical or wearable deployment: obtain appropriately governed paired
exposure/physiology data, assess movement and measurement quality, evaluate
participant- and song-held-out baselines, and conduct prospective safety and
causal evaluation with qualified oversight. DEAM has no patient preference,
dementia status, comfort, or HR-response labels.
