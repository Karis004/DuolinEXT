(function () {
  const LOG_PREFIX = "[Duolingo CN Words Export]";
  const USER_ID = "1148050773";
  const COURSE_ID = "fr";
  const LEARNING_LANGUAGE = "zh";
  const PAGE_SIZE = 50;
  const SORT_BY = "LEARNED_DATE";
  const BUTTON_ID = "duolingo-cn-words-export-button";

  console.log(LOG_PREFIX, "content script injected on:", window.location.href);

  function readCookie(name) {
    const match = document.cookie.match(new RegExp("(^|; )" + name + "=([^;]*)"));
    return match ? decodeURIComponent(match[2]) : "";
  }

  function buildHeaders(contentType) {
    const headers = {
      Accept: "application/json; charset=UTF-8",
      "X-Requested-With": "XMLHttpRequest",
      "X-Amzn-Trace-Id": "User=" + USER_ID
    };

    if (contentType) {
      headers["Content-Type"] = contentType;
    }

    const jwtToken = readCookie("jwt_token");
    if (jwtToken) {
      headers.Authorization = "Bearer " + jwtToken;
    }

    return headers;
  }

  function buildUserCourseUrl() {
    const fields = [
      "currentCourse",
      "currentCourseId",
      "learningLanguage",
      "fromLanguage"
    ].join(",");

    return (
      "https://www.duolingo.cn/2023-05-23/users/" +
      USER_ID +
      "?fields=" +
      encodeURIComponent(fields) +
      "&_=" +
      Date.now()
    );
  }

  function buildWordsUrl(startIndex) {
    return (
      "https://www.duolingo.cn/2017-06-30/users/" +
      USER_ID +
      "/courses/" +
      COURSE_ID +
      "/" +
      LEARNING_LANGUAGE +
      "/learned-lexemes?limit=" +
      PAGE_SIZE +
      "&sortBy=" +
      SORT_BY +
      "&startIndex=" +
      startIndex
    );
  }

  async function fetchCurrentCourse() {
    const url = buildUserCourseUrl();
    console.log(LOG_PREFIX, "fetching current course:", url);

    const response = await fetch(url, {
      method: "GET",
      credentials: "include",
      headers: buildHeaders()
    });

    const data = await response.json();
    console.log(LOG_PREFIX, "current course response summary:", {
      status: response.status,
      ok: response.ok,
      currentCourseId: data.currentCourseId,
      learningLanguage: data.learningLanguage,
      fromLanguage: data.fromLanguage,
      courseId: data.currentCourse && data.currentCourse.id,
      courseLearningLanguage: data.currentCourse && data.currentCourse.learningLanguage,
      courseFromLanguage: data.currentCourse && data.currentCourse.fromLanguage
    });
    console.log(LOG_PREFIX, "current course raw object:", data);

    return data;
  }

  function collectLevels(currentCourse) {
    const levels = [];
    const sections = Array.isArray(currentCourse.pathSectioned) ? currentCourse.pathSectioned : [];

    sections.forEach((section, sectionIndex) => {
      const units = Array.isArray(section.units) ? section.units : [];
      units.forEach((unit, unitIndex) => {
        const unitLevels = Array.isArray(unit.levels) ? unit.levels : [];
        unitLevels.forEach((level, levelIndex) => {
          levels.push({
            sectionIndex,
            unitIndex,
            levelIndex,
            level
          });
        });
      });
    });

    return levels;
  }

  function buildDynamicPayload(courseData) {
    const currentCourse = courseData.currentCourse;
    if (!currentCourse) {
      throw new Error("currentCourse missing from user course response");
    }

    const skillLevels = collectLevels(currentCourse)
      .map((entry) => {
        const level = entry.level;
        const skillId =
          level.pathLevelMetadata && level.pathLevelMetadata.skillId
            ? level.pathLevelMetadata.skillId
            : level.pathLevelClientData && level.pathLevelClientData.skillId;

        return {
          sectionIndex: entry.sectionIndex,
          unitIndex: entry.unitIndex,
          levelIndex: entry.levelIndex,
          id: level.id,
          type: level.type,
          subtype: level.subtype,
          state: level.state,
          debugName: level.debugName,
          skillId,
          crownLevelIndex: level.pathLevelMetadata && level.pathLevelMetadata.crownLevelIndex,
          finishedSessions: Number(level.finishedSessions || 0),
          totalSessions: Number(level.totalSessions || 0)
        };
      })
      .filter((item) => item.type === "skill")
      .filter((item) => item.subtype === "regular")
      .filter((item) => item.skillId)
      .filter((item) => item.state === "passed" || item.state === "active");

    const bySkillId = new Map();
    skillLevels.forEach((item) => {
      if (!bySkillId.has(item.skillId)) {
        bySkillId.set(item.skillId, []);
      }
      bySkillId.get(item.skillId).push(item);
    });

    const progressedSkills = Array.from(bySkillId.entries()).map(([skillId, rows]) => {
      rows.sort((a, b) => {
        if (a.sectionIndex !== b.sectionIndex) return a.sectionIndex - b.sectionIndex;
        if (a.unitIndex !== b.unitIndex) return a.unitIndex - b.unitIndex;
        if ((a.crownLevelIndex || 0) !== (b.crownLevelIndex || 0)) {
          return (a.crownLevelIndex || 0) - (b.crownLevelIndex || 0);
        }
        return a.levelIndex - b.levelIndex;
      });

      const currentRow = rows[rows.length - 1];
      return {
        finishedLevels: Number(currentRow.crownLevelIndex || 0),
        finishedSessions: Number(currentRow.finishedSessions || 0),
        skillId: {
          id: skillId
        }
      };
    });

    const payload = {
      lastTotalLexemeCount: 0,
      progressedSkills
    };

    console.log(LOG_PREFIX, "regular skill levels extracted from currentCourse:");
    console.table(skillLevels.map((item) => ({
      section: item.sectionIndex,
      unit: item.unitIndex,
      level: item.levelIndex,
      debugName: item.debugName,
      skillId: item.skillId,
      crownLevelIndex: item.crownLevelIndex,
      state: item.state,
      finishedSessions: item.finishedSessions,
      totalSessions: item.totalSessions
    })));

    console.log(LOG_PREFIX, "dynamic learned-lexemes payload:");
    console.log(payload);
    console.table(payload.progressedSkills.map((skill, index) => ({
      index,
      skillId: skill.skillId.id,
      finishedLevels: skill.finishedLevels,
      finishedSessions: skill.finishedSessions
    })));

    return payload;
  }

  async function fetchOneWordsPage(startIndex, payload) {
    const url = buildWordsUrl(startIndex);
    const response = await fetch(url, {
      method: "POST",
      credentials: "include",
      headers: buildHeaders("application/json; charset=UTF-8"),
      body: JSON.stringify(payload)
    });

    const responseText = await response.text();
    let data = null;
    try {
      data = JSON.parse(responseText);
    } catch (error) {
      data = {
        learnedLexemes: [],
        pagination: null,
        errorText: responseText,
        parseError: String(error)
      };
    }

    console.log(LOG_PREFIX, "words page response:", {
      startIndex,
      status: response.status,
      ok: response.ok,
      receivedWords: Array.isArray(data.learnedLexemes) ? data.learnedLexemes.length : 0,
      totalLexemes: data.pagination && data.pagination.totalLexemes,
      nextStartIndex: data.pagination && data.pagination.nextStartIndex,
      errorText: data.errorText ? data.errorText.slice(0, 160) : ""
    });

    return data;
  }

  async function fetchAllWords(payload) {
    const pages = [];
    const learnedLexemes = [];
    let startIndex = 0;

    while (startIndex !== null && startIndex !== undefined) {
      const data = await fetchOneWordsPage(startIndex, payload);
      const pageWords = Array.isArray(data.learnedLexemes) ? data.learnedLexemes : [];

      pageWords.forEach((word) => {
        learnedLexemes.push({
          index: learnedLexemes.length,
          ...word
        });
      });

      pages.push(data);
      startIndex = data.pagination && data.pagination.nextStartIndex;
    }

    const result = {
      fetchedAt: new Date().toISOString(),
      totalLexemes: pages[0] && pages[0].pagination ? pages[0].pagination.totalLexemes : learnedLexemes.length,
      fetchedCount: learnedLexemes.length,
      pageCount: pages.length,
      payload,
      pages,
      learnedLexemes
    };

    console.log(LOG_PREFIX, "final words result summary:", {
      totalLexemes: result.totalLexemes,
      fetchedCount: result.fetchedCount,
      pageCount: result.pageCount,
      firstWords: learnedLexemes.slice(0, 10).map((word) => word.text).join(" | "),
      lastWords: learnedLexemes.slice(Math.max(0, learnedLexemes.length - 10)).map((word) => word.text).join(" | ")
    });
    console.log(LOG_PREFIX, "final merged words result:", result);
    console.table(learnedLexemes.map((word) => ({
      index: word.index,
      text: word.text,
      translations: Array.isArray(word.translations) ? word.translations.join(", ") : "",
      audioURL: word.audioURL,
      isNew: word.isNew
    })));

    return result;
  }

  async function runExport(button) {
    console.log(LOG_PREFIX, "button clicked. Starting dynamic flow.");
    button.disabled = true;
    button.textContent = "Running...";

    try {
      const courseData = await fetchCurrentCourse();
      const payload = buildDynamicPayload(courseData);
      const result = await fetchAllWords(payload);
      button.textContent = "Fetched " + result.fetchedCount;
      console.log(LOG_PREFIX, "dynamic flow finished.");
    } catch (error) {
      button.textContent = "Failed";
      console.error(LOG_PREFIX, "dynamic flow failed:", error);
    } finally {
      window.setTimeout(() => {
        button.disabled = false;
        button.textContent = "Fetch words";
      }, 1500);
    }
  }

  function createButton() {
    if (document.getElementById(BUTTON_ID)) {
      console.log(LOG_PREFIX, "button already exists, skip creating another one.");
      return;
    }

    const button = document.createElement("button");
    button.id = BUTTON_ID;
    button.type = "button";
    button.textContent = "Fetch words";
    button.style.position = "fixed";
    button.style.right = "20px";
    button.style.bottom = "20px";
    button.style.zIndex = "2147483647";
    button.style.padding = "12px 16px";
    button.style.border = "0";
    button.style.borderRadius = "8px";
    button.style.background = "#58cc02";
    button.style.color = "#ffffff";
    button.style.fontSize = "15px";
    button.style.fontWeight = "700";
    button.style.cursor = "pointer";
    button.style.boxShadow = "0 6px 18px rgba(0, 0, 0, 0.22)";
    button.addEventListener("click", () => runExport(button));

    document.documentElement.appendChild(button);
    console.log(LOG_PREFIX, "button inserted:", button);
  }

  createButton();
})();
