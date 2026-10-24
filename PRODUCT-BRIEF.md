# Audio and physiological response: revised hackathon MVP

Status: revised October 24, 2026 following the user's clarification. This replaces the earlier preference-first MVP. No model training or dataset download has started.

## Intended product and training objective

Use existing paired audio and physiological recordings to train a small model that predicts a listener's short-term heart-rate response to an audio excerpt. Use its predictions to rank candidate audio, then eventually personalize using observed responses from a wearable. Live Garmin data is an integration step after offline training and evaluation.

The research goal is audio associated with settling toward a person's resting state. Do not optimize for the lowest possible heart rate or interpret every reduction as comfort or a clinical benefit. Self-reported comfort and Stop remain constraints and secondary outcomes; they do not replace measured physiological training labels.

The product hypothesis is that audio characteristics and a listener's initial physiological state improve response prediction over a constant or initial-state-only predictor. Patient need and clinical effectiveness remain unvalidated. Demonstrate the engineering using recorded data and consenting teammates, without claims of treatment.

## Clarify the audio representation

Pitch or frequency in Hz, rhythm or tempo in BPM, spectral balance, and binaural-beat differences are different variables. Start with available audio excerpts and extracted features such as tempo, spectral centroid, band energy, loudness, and dynamics. Selecting a whole track does not isolate the causal effect of an individual frequency. A pure-tone experiment would require recordings in which those tones were actually manipulated with suitable controls.

Confirmed direction: model several audio features. Use these six candidate feature groups for the first experiment:

| Group | Initial representation |
| --- | --- |
| Tempo | Estimated BPM with an indicator for unreliable or absent beat estimates |
| Audio level | Mean RMS energy; record playback gain separately when known |
| Dynamics | Variation in audio level over the excerpt |
| Spectral brightness | Mean spectral centroid |
| Frequency balance | Relative energy in a few predefined bass, midrange, and treble bands |
| Event density | Number of detected onsets per second |

These are candidate predictors, not established physiological effects. Compute them over the exact excerpt aligned to the response label. Fit scaling and feature-selection steps using training data only. If RMS is normalized across playback candidates, retain original recording features where useful and document the normalization; original experiment volume remains unknown unless supplied by the dataset.

Keep starting HR, prior HR trend, available individual baseline, activity, and signal quality separate from audio features. Compare the combined model against a physiological-state-only model. Repeated physiological windows do not create independent acoustic diversity. In a small source such as SONATA, eight songs still represent only eight distinct whole-excerpt audio examples; broader candidate datasets are listed below. Avoid interpreting feature importance as evidence of effective frequency bands.

## Dataset findings from original sources

The expanded shortlist corrects the earlier SONATA-centered plan. Source descriptions are verified; actual archives, exact audio alignment and usable reference windows have not been inspected.

- HKU956 is the first lead to inspect: 30 participants, 956 aligned listening records and 592 unique music tracks, with HR, BVP, inter-beat intervals, EDA and temperature. Full audio bundling remains unverified, and dataset metadata specifies CC BY-NC 4.0. [Creator repository](https://datahub.hku.hk/articles/dataset/HKU956_Dataset_in_Detecting_Music-Induced_Emotion_Based_on_Acoustic_Analysis_and_Physiological_Sensing_A_Multimodal_Approach_/21080821)
- Music-Mouv provides 35-participant wearable physiology, published music/HRV/EDA feature tables and a 282 MB raw release. Its gait-initiation setting introduces movement confounding; exact stimulus audio and clean reference availability need inspection. [Creator page](https://homepages.loria.fr/gbonnin/music-mouv/)
- SONATA records 50 healthy adults listening to eight musical excerpts with ECG, PPG, respiration, EDA and temperature. Its reference used whale sounds, not silence. It is a smaller complementary candidate, not the only possible training source. [Dataset](https://zenodo.org/records/22641079)
- DEAP pairs 40 music videos with 32 participants' physiology, including PPG; the original paper documents audio features. Visual exposure and uncertain current download access limit its role as an audio-only benchmark. [Original paper](https://www.researchgate.net/publication/232651717_DEAP_A_Database_for_Emotion_Analysis_Using_Physiological_Signals)
- PMEmo provides music, acoustic features and EDA, not established HR-response labels. DEAM provides perceived musical arousal/valence, not physiological response labels. [PMEmo](https://github.com/HuiZhangDB/PMEmo), [DEAM](https://cvml.unige.ch/databases/DEAM/)

Keep datasets separate initially. They cannot be arbitrarily joined across different people and studies to manufacture paired audio-heart-rate observations. Full comparison: [Audio physiology dataset alternatives](/Users/davidsu/reports/Audio%20physiology%20dataset%20alternatives.md).

## MVP pipeline

1. Inspect HKU956's actual audio/feature and physiological files, timing, reference coverage, and license first; inspect Music-Mouv next if needed. Keep SONATA as an additional source. Finalize the target only after alignment is demonstrated.
2. Build one observation per participant and exposure, with a prespecified response window. Initially predict later-window mean HR minus an earlier-window mean HR if those are the available aligned recordings. If a genuine pre-exposure baseline is available, prefer an explicit pre-versus-post definition. An early in-song window is already exposed and must be labeled accordingly.
3. Inputs: audio features, HR at the start of the prediction interval, its prior trend, and usable baseline-relative features. Add only physiological inputs that will also be available in the intended deployment, or report offline multimodal and wearable-compatible models separately.
4. Fit a regularized linear model first, then compare a small tree-based model if justified. Training data are small; a large neural network is unnecessary.
5. Hold out entire participants. Compare against a constant-change predictor and an initial-state-only predictor using mean absolute error. Report uncertainty and whether audio adds predictive value. Separate leave-song-out evaluation is required for claims about unfamiliar audio; report the distinct track count for the selected source and the limitations of small stimulus sets. Never randomly split adjacent windows from one session between train and test.
6. Rank available candidate excerpts by predicted HR response, with uncertainty and comfort constraints. A future controller can update person-specific response estimates from observed sessions. Do not present prediction as proof of causal lowering.
7. Build an operator view showing recorded input, source, candidate audio, predicted response, and actual held-out response. Patient-side playback is screenless. Support Stop and a clearly labeled simulated nurse-presence pause.

## Parameters to prioritize

Required: HR trajectory, exposure timestamps, signal-quality checks, and an explicitly defined reference window. Record activity or posture when available, because movement and recovery can otherwise confound response.

First additional physiological outcome: respiration rate, if reliably measured. Next: beat-to-beat HRV (for example RMSSD) if valid intervals and suitable consistent windows are available. Averaged BPM is insufficient to reconstruct HRV. EDA is optional complementary information, not a standalone anxiety measurement. Keep a brief comfort rating as a separate outcome.

On the audio side record pitch/spectral features, tempo, loudness, playback duration, and delivered volume when known. File loudness is not a calibrated measurement of the volume heard by a participant. Dataset physiological variables and Garmin-accessible variables must be distinguished.

## Demonstration and acceptance checks

The demo replays recorded held-out physiology, predicts responses for available excerpts, explains the selection, and shows the observed held-out result when available. Any synthetic stream, seeded personalization, or simulated presence is visibly identified. Do not animate a successful HR reduction as though the model caused it.

Completion requires reproducible preprocessing, an explicit target/window definition, a participant-held-out result against simple baselines, a limitation statement for unfamiliar tracks and hospitalized populations, audible playback, reliable Stop, and care-presence pausing. A model that fails to beat its baseline must be reported honestly.

## Order of work

1. Data inspection and target feasibility.
2. Feature extraction, a baseline model, and leakage-resistant evaluation.
3. Screenless playback and operator demonstration using recorded input.
4. Live Garmin integration and personalization if time permits.

A randomized silence or rest comparison is future causal evaluation. A 24-participant music study found HR, breathing, and other responses varied with tempo and pauses; its findings motivate controls rather than universal frequency prescriptions. Source: https://pubmed.ncbi.nlm.nih.gov/16199412/

Measurement guidance for HR/HRV, respiratory influences, interval quality, and standardized windows: https://doi.org/10.1111/psyp.14604

Go: a bounded offline response-prediction experiment, subject to file inspection. The go/no-go point is whether paired usable recordings and audio features exist. If they do not, report the limitation and revise the training target openly rather than substituting synthetic efficacy labels.
