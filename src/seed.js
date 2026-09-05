import fs from 'node:fs';
import path from 'node:path';
import { ROOT } from './config.js';
import * as store from './store.js';
import { scoreTranscript } from './score.js';
import { renderReport } from './report.js';
import { formatTranscript } from './transcribe.js';

/**
 * Fill the dashboard with sample practice calls so the trends and rollups are
 * visible before real calls are flowing.
 *
 * The scores are NOT fabricated - every session is a real transcript run through
 * the real scorer. Variation comes from adding or withholding the exchanges that
 * the rubric actually rewards, and reps get better over time because the later
 * calls include more of them. Delete data/ to clear it all out.
 */

const REPS = ['Marcus', 'Dani', 'Seth'];

/** Each improvement is the exchange a rep starts having once they have been coached on it. */
const IMPROVEMENTS = {
  decision_makers: [
    { speaker: 'rep', text: 'Before we go further - is it just you on title, or is anyone else involved in the decision?' },
    { speaker: 'seller', text: 'My sister Denise is on it too. She is up in Sacramento.' },
    { speaker: 'rep', text: 'Got it. Would it make sense to get Denise on the next call so you two are hearing the same thing?' },
    { speaker: 'seller', text: 'Yeah, that is probably smart. I will see when she is free.' },
  ],
  timeline: [
    { speaker: 'rep', text: 'When you say sometime this year - is there anything that makes one month better than another? Taxes, the insurance renewal, anything like that?' },
    { speaker: 'seller', text: 'Well, the insurance is up in October and they already told me they might not renew it vacant.' },
    { speaker: 'rep', text: 'So realistically you would want this handled before October.' },
    { speaker: 'seller', text: 'Yeah. I had not really thought about it that way, but yes.' },
  ],
  objection: [
    { speaker: 'rep', text: 'That is fair, and I hear that a lot. Help me understand - when you saw the one point one, was that for a house in this condition or an updated one?' },
    { speaker: 'seller', text: 'I mean, I did not really look at that. It just gave me the number.' },
    { speaker: 'rep', text: 'Right. So the question is really what it is worth as it sits today, with the roof and the bathroom. Can I walk you through how I get to my number?' },
    { speaker: 'seller', text: 'Sure, go ahead.' },
  ],
  motivation: [
    { speaker: 'rep', text: 'You mentioned the driving is wearing on you. If nothing changed and you were still driving over there next spring, what does that look like for you?' },
    { speaker: 'seller', text: 'Honestly? I would be pretty resentful about it. It is already eating my Saturdays.' },
  ],
};

const ORDER = ['decision_makers', 'timeline', 'objection', 'motivation'];

/** Splice improvement exchanges in before the closing turns so the call still ends naturally. */
function buildTranscript(base, improvements) {
  const turns = base.turns.map((t) => ({ ...t }));
  const tail = turns.splice(-6);
  for (const key of improvements) turns.push(...IMPROVEMENTS[key].map((t) => ({ ...t })));
  turns.push(...tail);

  let clock = 0;
  return {
    diarized: true,
    source: 'sample',
    turns: turns.map((t) => {
      const words = t.text.split(/\s+/).length;
      const start = clock;
      clock += Math.max(3, Math.round(words / 2.6));
      return { speaker: t.speaker, text: t.text, start, end: clock };
    }),
  };
}

export async function seedSessions(count = 12) {
  const base = JSON.parse(fs.readFileSync(path.join(ROOT, 'samples', 'sample-practice-call.json'), 'utf8'));
  const perRep = Math.max(2, Math.ceil(count / REPS.length));
  const created = [];

  for (const [repIndex, rep] of REPS.entries()) {
    for (let i = 0; i < perRep && created.length < count; i++) {
      // Later calls carry more of the coached behaviors, so the trend line earns its shape.
      const learned = Math.min(ORDER.length, Math.floor((i / Math.max(1, perRep - 1)) * (ORDER.length + 1) - repIndex * 0.5));
      const improvements = ORDER.slice(0, Math.max(0, learned));
      const transcript = buildTranscript(base, improvements);

      const daysAgo = (perRep - i) * 5 + repIndex;
      const createdAt = new Date(Date.now() - daysAgo * 24 * 60 * 60 * 1000).toISOString();
      const difficulty = i === 0 ? 'easy' : i < perRep - 1 ? 'medium' : 'hard';

      const session = store.createSession({
        id: store.newSessionId('sample'),
        persona: 'larry',
        rep,
        difficulty,
        status: 'transcribed',
        created_at: createdAt,
        duration_seconds: transcript.turns[transcript.turns.length - 1].end,
        sample_data: true,
      });

      store.saveArtifact(session.id, 'transcript.json', { session_id: session.id, ...transcript });
      store.saveArtifact(session.id, 'transcript.txt', formatTranscript(transcript));
      const scorecard = await scoreTranscript(session.id);
      renderReport(session.id);
      store.updateSession(session.id, { status: 'scored', score: scorecard.overall_score, created_at: createdAt });

      created.push({ id: session.id, rep, score: scorecard.overall_score, improvements: improvements.length, difficulty });
    }
  }

  return created;
}
