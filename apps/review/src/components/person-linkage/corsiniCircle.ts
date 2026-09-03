// Generated read-only from main.db on 2026-09-02 by scratchpad/gen_circle.py (session 2026-09-02).
// The business circle of records 5305 and 11633 (Andrea di Neri Corsini): every co-investor
// on their acts, with the acts, each partner's total contract count (hub rule: > 20 ignored),
// and the rarest words of every firm name with their document frequency across all firm names.

export type CircleAct = { with: number; contract: number; year: number; firm: string };
export type CirclePartner = { id: number; name: string; contracts_total: number; acts: CircleAct[]; shared: boolean };
export type CircleCandidate = { id: number; name: string; active: string; acts: number; role: string; contracts_total: number };
export type CircleData = {
  generated: string; source: string; hub_threshold: number; shared_firm: string;
  candidates: CircleCandidate[];
  candidate_acts: Record<string, Array<{ contract: number; year: number; firm: string }>>;
  partners: CirclePartner[];
  firms: Record<string, Array<{ word: string; df: number }>>;
};

export const CORSINI_CIRCLE: CircleData = {
  "generated": "2026-09-02",
  "source": "main.db",
  "hub_threshold": 20,
  "shared_firm": "Benedetto Nomi e Tommaso Pandolfini e compagni",
  "candidates": [
    {
      "id": 5305,
      "name": "Andrea di Neri Corsini",
      "active": "1631–1644",
      "acts": 5,
      "role": "external investor in all 5",
      "contracts_total": 5
    },
    {
      "id": 11633,
      "name": "Andrea di Neri di Lorenzo Corsini",
      "active": "1645–1657",
      "acts": 3,
      "role": "external investor in all 3",
      "contracts_total": 3
    }
  ],
  "candidate_acts": {
    "5305": [
      {
        "contract": 2522,
        "year": 1631,
        "firm": "Ugolino Mannelli"
      },
      {
        "contract": 2571,
        "year": 1634,
        "firm": "Ottavio Mannelli, Cesare Ricciardi e compagni"
      },
      {
        "contract": 2614,
        "year": 1636,
        "firm": "Francesco Cafferelli e compagni"
      },
      {
        "contract": 2739,
        "year": 1638,
        "firm": "Benedetto Nomi e Tommaso Pandolfini e compagni"
      },
      {
        "contract": 3513,
        "year": 1644,
        "firm": "Piero Baroni e compagni"
      }
    ],
    "11633": [
      {
        "contract": 4037,
        "year": 1645,
        "firm": "Benedetto Nomi e Tommaso Pandolfini e compagni"
      },
      {
        "contract": 3556,
        "year": 1652,
        "firm": "Benedetto Nomi e Giovanni Casini e compagni"
      },
      {
        "contract": 3635,
        "year": 1657,
        "firm": "Giovanni Casini e Simone Acciaioli e compagni"
      }
    ]
  },
  "partners": [
    {
      "id": 11292,
      "name": "Filippo di Lorenzo Corsini",
      "contracts_total": 24,
      "acts": [
        {
          "with": 5305,
          "contract": 2522,
          "year": 1631,
          "firm": "Ugolino Mannelli"
        }
      ],
      "shared": false
    },
    {
      "id": 11030,
      "name": "Ugolino Mannelli",
      "contracts_total": 2,
      "acts": [
        {
          "with": 5305,
          "contract": 2522,
          "year": 1631,
          "firm": "Ugolino Mannelli"
        }
      ],
      "shared": false
    },
    {
      "id": 6461,
      "name": "Giovanni Corsi",
      "contracts_total": 14,
      "acts": [
        {
          "with": 5305,
          "contract": 2522,
          "year": 1631,
          "firm": "Ugolino Mannelli"
        }
      ],
      "shared": false
    },
    {
      "id": 3915,
      "name": "Simone Giugni",
      "contracts_total": 5,
      "acts": [
        {
          "with": 5305,
          "contract": 2522,
          "year": 1631,
          "firm": "Ugolino Mannelli"
        }
      ],
      "shared": false
    },
    {
      "id": 11319,
      "name": "Francesco Falconieri",
      "contracts_total": 1,
      "acts": [
        {
          "with": 5305,
          "contract": 2522,
          "year": 1631,
          "firm": "Ugolino Mannelli"
        }
      ],
      "shared": false
    },
    {
      "id": 4384,
      "name": "Tommaso Mannelli",
      "contracts_total": 3,
      "acts": [
        {
          "with": 5305,
          "contract": 2571,
          "year": 1634,
          "firm": "Ottavio Mannelli, Cesare Ricciardi e compagni"
        }
      ],
      "shared": false
    },
    {
      "id": 5303,
      "name": "Cesare Riccardi",
      "contracts_total": 1,
      "acts": [
        {
          "with": 5305,
          "contract": 2571,
          "year": 1634,
          "firm": "Ottavio Mannelli, Cesare Ricciardi e compagni"
        }
      ],
      "shared": false
    },
    {
      "id": 5269,
      "name": "Filippo Corsini",
      "contracts_total": 14,
      "acts": [
        {
          "with": 5305,
          "contract": 2571,
          "year": 1634,
          "firm": "Ottavio Mannelli, Cesare Ricciardi e compagni"
        }
      ],
      "shared": false
    },
    {
      "id": 11463,
      "name": "Giovanni di Jacopo Corsi",
      "contracts_total": 7,
      "acts": [
        {
          "with": 5305,
          "contract": 2571,
          "year": 1634,
          "firm": "Ottavio Mannelli, Cesare Ricciardi e compagni"
        }
      ],
      "shared": false
    },
    {
      "id": 11027,
      "name": "Ottavio Mannelli",
      "contracts_total": 1,
      "acts": [
        {
          "with": 5305,
          "contract": 2571,
          "year": 1634,
          "firm": "Ottavio Mannelli, Cesare Ricciardi e compagni"
        }
      ],
      "shared": false
    },
    {
      "id": 5411,
      "name": "Francesco Cafferelli",
      "contracts_total": 1,
      "acts": [
        {
          "with": 5305,
          "contract": 2614,
          "year": 1636,
          "firm": "Francesco Cafferelli e compagni"
        }
      ],
      "shared": false
    },
    {
      "id": 6463,
      "name": "Ottaviano Acciaioli",
      "contracts_total": 15,
      "acts": [
        {
          "with": 5305,
          "contract": 2614,
          "year": 1636,
          "firm": "Francesco Cafferelli e compagni"
        },
        {
          "with": 5305,
          "contract": 2614,
          "year": 1636,
          "firm": "Francesco Cafferelli e compagni"
        }
      ],
      "shared": false
    },
    {
      "id": 6465,
      "name": "Marco Martelli",
      "contracts_total": 17,
      "acts": [
        {
          "with": 5305,
          "contract": 2614,
          "year": 1636,
          "firm": "Francesco Cafferelli e compagni"
        }
      ],
      "shared": false
    },
    {
      "id": 4166,
      "name": "Leonardo Tempi",
      "contracts_total": 9,
      "acts": [
        {
          "with": 5305,
          "contract": 2614,
          "year": 1636,
          "firm": "Francesco Cafferelli e compagni"
        }
      ],
      "shared": false
    },
    {
      "id": 5418,
      "name": "Niccolò Buonaccorsi",
      "contracts_total": 4,
      "acts": [
        {
          "with": 5305,
          "contract": 2614,
          "year": 1636,
          "firm": "Francesco Cafferelli e compagni"
        }
      ],
      "shared": false
    },
    {
      "id": 5419,
      "name": "Giulio Buonaccorsi",
      "contracts_total": 5,
      "acts": [
        {
          "with": 5305,
          "contract": 2614,
          "year": 1636,
          "firm": "Francesco Cafferelli e compagni"
        }
      ],
      "shared": false
    },
    {
      "id": 5420,
      "name": "Lorenzo Buonaccorsi",
      "contracts_total": 7,
      "acts": [
        {
          "with": 5305,
          "contract": 2614,
          "year": 1636,
          "firm": "Francesco Cafferelli e compagni"
        }
      ],
      "shared": false
    },
    {
      "id": 5190,
      "name": "Filippo di Neri Corsini",
      "contracts_total": 2,
      "acts": [
        {
          "with": 5305,
          "contract": 2614,
          "year": 1636,
          "firm": "Francesco Cafferelli e compagni"
        }
      ],
      "shared": false
    },
    {
      "id": 5413,
      "name": "Jacopo di Neri Corsini",
      "contracts_total": 2,
      "acts": [
        {
          "with": 5305,
          "contract": 2614,
          "year": 1636,
          "firm": "Francesco Cafferelli e compagni"
        },
        {
          "with": 5305,
          "contract": 2739,
          "year": 1638,
          "firm": "Benedetto Nomi e Tommaso Pandolfini e compagni"
        }
      ],
      "shared": false
    },
    {
      "id": 4240,
      "name": "Piero di Neri di Piero Capponi",
      "contracts_total": 6,
      "acts": [
        {
          "with": 5305,
          "contract": 2614,
          "year": 1636,
          "firm": "Francesco Cafferelli e compagni"
        }
      ],
      "shared": false
    },
    {
      "id": 5758,
      "name": "Benedetto di Marco Nomi",
      "contracts_total": 4,
      "acts": [
        {
          "with": 5305,
          "contract": 2739,
          "year": 1638,
          "firm": "Benedetto Nomi e Tommaso Pandolfini e compagni"
        },
        {
          "with": 11633,
          "contract": 4037,
          "year": 1645,
          "firm": "Benedetto Nomi e Tommaso Pandolfini e compagni"
        },
        {
          "with": 11633,
          "contract": 3556,
          "year": 1652,
          "firm": "Benedetto Nomi e Giovanni Casini e compagni"
        }
      ],
      "shared": true
    },
    {
      "id": 5760,
      "name": "Tommaso di Pier Filippo Pandolfini",
      "contracts_total": 2,
      "acts": [
        {
          "with": 5305,
          "contract": 2739,
          "year": 1638,
          "firm": "Benedetto Nomi e Tommaso Pandolfini e compagni"
        },
        {
          "with": 11633,
          "contract": 4037,
          "year": 1645,
          "firm": "Benedetto Nomi e Tommaso Pandolfini e compagni"
        }
      ],
      "shared": true
    },
    {
      "id": 5761,
      "name": "Bartolomeo di Filippo Corsini",
      "contracts_total": 3,
      "acts": [
        {
          "with": 5305,
          "contract": 2739,
          "year": 1638,
          "firm": "Benedetto Nomi e Tommaso Pandolfini e compagni"
        }
      ],
      "shared": false
    },
    {
      "id": 5763,
      "name": "Neri di Filippo Corsini",
      "contracts_total": 1,
      "acts": [
        {
          "with": 5305,
          "contract": 2739,
          "year": 1638,
          "firm": "Benedetto Nomi e Tommaso Pandolfini e compagni"
        }
      ],
      "shared": false
    },
    {
      "id": 4755,
      "name": "Lorenzo di Carlo di Lorenzo Franceschi",
      "contracts_total": 8,
      "acts": [
        {
          "with": 5305,
          "contract": 2739,
          "year": 1638,
          "firm": "Benedetto Nomi e Tommaso Pandolfini e compagni"
        }
      ],
      "shared": false
    },
    {
      "id": 4756,
      "name": "Luca di Carlo di Lorenzo Franceschi",
      "contracts_total": 10,
      "acts": [
        {
          "with": 5305,
          "contract": 2739,
          "year": 1638,
          "firm": "Benedetto Nomi e Tommaso Pandolfini e compagni"
        },
        {
          "with": 11633,
          "contract": 3635,
          "year": 1657,
          "firm": "Giovanni Casini e Simone Acciaioli e compagni"
        }
      ],
      "shared": true
    },
    {
      "id": 4757,
      "name": "Agostino di Carlo di Lorenzo Franceschi",
      "contracts_total": 7,
      "acts": [
        {
          "with": 5305,
          "contract": 2739,
          "year": 1638,
          "firm": "Benedetto Nomi e Tommaso Pandolfini e compagni"
        },
        {
          "with": 11633,
          "contract": 3635,
          "year": 1657,
          "firm": "Giovanni Casini e Simone Acciaioli e compagni"
        }
      ],
      "shared": true
    },
    {
      "id": 6736,
      "name": "Carlo di Pier Francesco Rinuccini",
      "contracts_total": 11,
      "acts": [
        {
          "with": 5305,
          "contract": 2739,
          "year": 1638,
          "firm": "Benedetto Nomi e Tommaso Pandolfini e compagni"
        },
        {
          "with": 11633,
          "contract": 4037,
          "year": 1645,
          "firm": "Benedetto Nomi e Tommaso Pandolfini e compagni"
        },
        {
          "with": 11633,
          "contract": 3556,
          "year": 1652,
          "firm": "Benedetto Nomi e Giovanni Casini e compagni"
        },
        {
          "with": 11633,
          "contract": 3635,
          "year": 1657,
          "firm": "Giovanni Casini e Simone Acciaioli e compagni"
        }
      ],
      "shared": true
    },
    {
      "id": 6737,
      "name": "Giovanni di Pier Francesco Rinuccini",
      "contracts_total": 11,
      "acts": [
        {
          "with": 5305,
          "contract": 2739,
          "year": 1638,
          "firm": "Benedetto Nomi e Tommaso Pandolfini e compagni"
        },
        {
          "with": 11633,
          "contract": 4037,
          "year": 1645,
          "firm": "Benedetto Nomi e Tommaso Pandolfini e compagni"
        },
        {
          "with": 11633,
          "contract": 3556,
          "year": 1652,
          "firm": "Benedetto Nomi e Giovanni Casini e compagni"
        },
        {
          "with": 11633,
          "contract": 3635,
          "year": 1657,
          "firm": "Giovanni Casini e Simone Acciaioli e compagni"
        }
      ],
      "shared": true
    },
    {
      "id": 5764,
      "name": "Vincentio di Giovanni Battista Benedetti",
      "contracts_total": 8,
      "acts": [
        {
          "with": 5305,
          "contract": 2739,
          "year": 1638,
          "firm": "Benedetto Nomi e Tommaso Pandolfini e compagni"
        },
        {
          "with": 11633,
          "contract": 4037,
          "year": 1645,
          "firm": "Benedetto Nomi e Tommaso Pandolfini e compagni"
        },
        {
          "with": 11633,
          "contract": 3556,
          "year": 1652,
          "firm": "Benedetto Nomi e Giovanni Casini e compagni"
        },
        {
          "with": 11633,
          "contract": 3635,
          "year": 1657,
          "firm": "Giovanni Casini e Simone Acciaioli e compagni"
        }
      ],
      "shared": true
    },
    {
      "id": 5249,
      "name": "Piero di Antonio Baroni",
      "contracts_total": 3,
      "acts": [
        {
          "with": 5305,
          "contract": 3513,
          "year": 1644,
          "firm": "Piero Baroni e compagni"
        }
      ],
      "shared": false
    },
    {
      "id": 10975,
      "name": "Agostino di Carlo Franceschi",
      "contracts_total": 8,
      "acts": [
        {
          "with": 11633,
          "contract": 4037,
          "year": 1645,
          "firm": "Benedetto Nomi e Tommaso Pandolfini e compagni"
        },
        {
          "with": 11633,
          "contract": 3556,
          "year": 1652,
          "firm": "Benedetto Nomi e Giovanni Casini e compagni"
        }
      ],
      "shared": false
    },
    {
      "id": 10976,
      "name": "Luca di Carlo Franceschi",
      "contracts_total": 8,
      "acts": [
        {
          "with": 11633,
          "contract": 4037,
          "year": 1645,
          "firm": "Benedetto Nomi e Tommaso Pandolfini e compagni"
        },
        {
          "with": 11633,
          "contract": 3556,
          "year": 1652,
          "firm": "Benedetto Nomi e Giovanni Casini e compagni"
        }
      ],
      "shared": false
    },
    {
      "id": 7599,
      "name": "Lorenzo di Carlo Franceschi",
      "contracts_total": 11,
      "acts": [
        {
          "with": 11633,
          "contract": 4037,
          "year": 1645,
          "firm": "Benedetto Nomi e Tommaso Pandolfini e compagni"
        },
        {
          "with": 11633,
          "contract": 3556,
          "year": 1652,
          "firm": "Benedetto Nomi e Giovanni Casini e compagni"
        },
        {
          "with": 11633,
          "contract": 3635,
          "year": 1657,
          "firm": "Giovanni Casini e Simone Acciaioli e compagni"
        }
      ],
      "shared": false
    },
    {
      "id": 6997,
      "name": "Andrea Gherardi",
      "contracts_total": 4,
      "acts": [
        {
          "with": 11633,
          "contract": 4037,
          "year": 1645,
          "firm": "Benedetto Nomi e Tommaso Pandolfini e compagni"
        }
      ],
      "shared": false
    },
    {
      "id": 6999,
      "name": "Raffaello Gherardi",
      "contracts_total": 3,
      "acts": [
        {
          "with": 11633,
          "contract": 4037,
          "year": 1645,
          "firm": "Benedetto Nomi e Tommaso Pandolfini e compagni"
        }
      ],
      "shared": false
    },
    {
      "id": 11631,
      "name": "Bartolomeo di Filippo di Lorenzo Corsini",
      "contracts_total": 7,
      "acts": [
        {
          "with": 11633,
          "contract": 4037,
          "year": 1645,
          "firm": "Benedetto Nomi e Tommaso Pandolfini e compagni"
        },
        {
          "with": 11633,
          "contract": 3556,
          "year": 1652,
          "firm": "Benedetto Nomi e Giovanni Casini e compagni"
        },
        {
          "with": 11633,
          "contract": 3635,
          "year": 1657,
          "firm": "Giovanni Casini e Simone Acciaioli e compagni"
        }
      ],
      "shared": false
    },
    {
      "id": 11632,
      "name": "Neri di Filippo di Lorenzo Corsini",
      "contracts_total": 2,
      "acts": [
        {
          "with": 11633,
          "contract": 4037,
          "year": 1645,
          "firm": "Benedetto Nomi e Tommaso Pandolfini e compagni"
        }
      ],
      "shared": false
    },
    {
      "id": 7593,
      "name": "Giovanni di Zanobi Casini",
      "contracts_total": 6,
      "acts": [
        {
          "with": 11633,
          "contract": 3556,
          "year": 1652,
          "firm": "Benedetto Nomi e Giovanni Casini e compagni"
        },
        {
          "with": 11633,
          "contract": 3635,
          "year": 1657,
          "firm": "Giovanni Casini e Simone Acciaioli e compagni"
        }
      ],
      "shared": false
    },
    {
      "id": 7734,
      "name": "Simone di Mario Acciaioli",
      "contracts_total": 3,
      "acts": [
        {
          "with": 11633,
          "contract": 3635,
          "year": 1657,
          "firm": "Giovanni Casini e Simone Acciaioli e compagni"
        }
      ],
      "shared": false
    },
    {
      "id": 7735,
      "name": "Michele di Priore Lioni",
      "contracts_total": 1,
      "acts": [
        {
          "with": 11633,
          "contract": 3635,
          "year": 1657,
          "firm": "Giovanni Casini e Simone Acciaioli e compagni"
        }
      ],
      "shared": false
    }
  ],
  "firms": {
    "Ugolino Mannelli": [
      {
        "word": "ugolino",
        "df": 2
      },
      {
        "word": "mannelli",
        "df": 14
      }
    ],
    "Ottavio Mannelli, Cesare Ricciardi e compagni": [
      {
        "word": "ricciardi",
        "df": 5
      },
      {
        "word": "mannelli",
        "df": 14
      },
      {
        "word": "cesare",
        "df": 19
      }
    ],
    "Francesco Cafferelli e compagni": [
      {
        "word": "cafferelli",
        "df": 8
      },
      {
        "word": "francesco",
        "df": 471
      }
    ],
    "Benedetto Nomi e Tommaso Pandolfini e compagni": [
      {
        "word": "nomi",
        "df": 4
      },
      {
        "word": "pandolfini",
        "df": 26
      },
      {
        "word": "benedetto",
        "df": 66
      }
    ],
    "Piero Baroni e compagni": [
      {
        "word": "baroni",
        "df": 5
      },
      {
        "word": "piero",
        "df": 119
      }
    ],
    "Benedetto Nomi e Giovanni Casini e compagni": [
      {
        "word": "nomi",
        "df": 4
      },
      {
        "word": "casini",
        "df": 32
      },
      {
        "word": "benedetto",
        "df": 66
      }
    ],
    "Giovanni Casini e Simone Acciaioli e compagni": [
      {
        "word": "acciaioli",
        "df": 20
      },
      {
        "word": "casini",
        "df": 32
      },
      {
        "word": "simone",
        "df": 56
      }
    ]
  }
};
