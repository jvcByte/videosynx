import { Quota } from "../lib/api";
import { tierLabel } from "../lib/format";

/**
 * How much of today's allowance is left.
 *
 * The bar only appears once some of it is gone. A full meter at zero usage is
 * three quarters of a bar saying nothing, and the number already carries it.
 */
export function QuotaMeter({
  quota,
  failed,
}: {
  quota: Quota | null;
  failed: boolean;
}) {
  if (!quota) {
    // A failure has to look different from a wait. A placeholder that shimmers
    // forever is indistinguishable from one that is merely slow, which is how
    // a dead API came to look like a slow one.
    if (failed) {
      return (
        <div className="quota">
          <span className="quota-rest">Allowance unavailable</span>
        </div>
      );
    }
    // A placeholder, not a zero: showing "0 left today" while loading would be
    // a lie that looks exactly like having run out.
    return (
      <div className="quota quota-loading" aria-hidden>
        <span className="skeleton skeleton-line" />
      </div>
    );
  }

  const used = Math.min(quota.used_today, quota.jobs_per_day);
  const percent = quota.jobs_per_day ? (used / quota.jobs_per_day) * 100 : 0;

  return (
    <div className="quota">
      <span className="tier">{tierLabel(quota.tier)}</span>
      <span className="figure">
        {quota.remaining} of {quota.jobs_per_day}
      </span>
      <span className="quota-rest">left today</span>
      {/* Only once the allowance is gone, because that is the only moment it
          changes anything: "0 of 3" on its own reads as nothing working. */}
      {quota.remaining === 0 && quota.local_remaining > 0 && (
        <span className="quota-rest">
          · {quota.local_remaining} local renders
        </span>
      )}
      {used > 0 && (
        <span className="meter" aria-hidden>
          <span style={{ width: `${percent}%` }} />
        </span>
      )}
    </div>
  );
}
